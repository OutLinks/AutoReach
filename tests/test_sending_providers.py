from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.adapters.live import _load_agent

_load_agent("agent4-sender", "agent4_sender")

ServiceConfig = importlib.import_module("agent4_sender.config").ServiceConfig
OutgoingMessage = importlib.import_module(
    "agent4_sender.layers.sending.base"
).OutgoingMessage
MailgunProvider = importlib.import_module(
    "agent4_sender.layers.sending.mailgun"
).MailgunProvider
PostmarkProvider = importlib.import_module(
    "agent4_sender.layers.sending.postmark"
).PostmarkProvider
ResendProvider = importlib.import_module(
    "agent4_sender.layers.sending.resend"
).ResendProvider
SendingLayer = importlib.import_module(
    "agent4_sender.layers.sending.sender"
).SendingLayer
SendGridProvider = importlib.import_module(
    "agent4_sender.layers.sending.sendgrid"
).SendGridProvider
SmtpProvider = importlib.import_module(
    "agent4_sender.layers.sending.smtp"
).SmtpProvider
SendingAccount = importlib.import_module("agent4_sender.models").SendingAccount
SentEmail = importlib.import_module("agent4_sender.models").SentEmail
SendStore = importlib.import_module("agent4_sender.storage.send_store").SendStore


class FakeResponse:
    def __init__(self, data=None, headers=None) -> None:
        self._data = data or {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeSmtp:
    def __init__(self, supports_starttls: bool = True) -> None:
        self.supports_starttls = supports_starttls
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def has_extn(self, name: str) -> bool:
        return name == "STARTTLS" and self.supports_starttls

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")

    def send_message(self, mime) -> None:
        self.calls.append("send_message")


def message(body: str = "<p>Hello</p>") -> OutgoingMessage:
    return OutgoingMessage(
        to_email="lead@example.com",
        from_email="sender@example.org",
        from_name="Auto Reach",
        subject="Hello",
        body=body,
        reply_to="reply@example.org",
        in_reply_to="<parent@example.org>",
        references="<root@example.org> <parent@example.org>",
    )


class ProviderRegistryTests(unittest.TestCase):
    def test_all_documented_providers_are_registered(self) -> None:
        layer = SendingLayer(ServiceConfig(simulate=True))

        expected = {
            "gmail",
            "instantly",
            "mailgun",
            "outreach",
            "postmark",
            "resend",
            "sendgrid",
            "ses",
            "smtp",
        }
        self.assertEqual(set(layer._providers), expected)
        for provider in expected:
            account = SendingAccount(email="sender@example.org", provider=provider)
            self.assertEqual(layer._provider_for(account).name, provider)


class SmtpTransportTests(unittest.TestCase):
    def test_submission_port_requires_and_uses_starttls(self) -> None:
        transport = FakeSmtp()
        with patch("smtplib.SMTP", return_value=transport):
            SmtpProvider._deliver("smtp.example.org", 587, "user", "secret", object())
        self.assertEqual(transport.calls, ["ehlo", "starttls", "ehlo", "login", "send_message"])

    def test_implicit_tls_port_uses_smtp_ssl(self) -> None:
        transport = FakeSmtp()
        with patch("smtplib.SMTP_SSL", return_value=transport) as ssl, patch("smtplib.SMTP") as plain:
            SmtpProvider._deliver("smtp.example.org", 465, "", "", object())
        ssl.assert_called_once_with("smtp.example.org", 465, timeout=30)
        plain.assert_not_called()
        self.assertEqual(transport.calls, ["ehlo", "send_message"])

    def test_submission_port_rejects_plaintext_fallback(self) -> None:
        transport = FakeSmtp(supports_starttls=False)
        with patch("smtplib.SMTP", return_value=transport):
            with self.assertRaisesRegex(RuntimeError, "STARTTLS"):
                SmtpProvider._deliver("smtp.example.org", 587, "user", "secret", object())


class SenderStoreTests(unittest.TestCase):
    def test_idempotency_key_is_unique_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SendStore(str(Path(directory) / "sends.db"))
            sent = SentEmail(email_id="email-1", lead_id="lead-1", idempotency_key="email-1:day0")
            store.insert_sent(sent)
            found = store.get_sent_by_idempotency_key("email-1:day0")
            self.assertEqual(found["id"], sent.id)
            duplicate = SentEmail(email_id="email-2", lead_id="lead-2", idempotency_key="email-1:day0")
            with self.assertRaises(Exception):
                store.insert_sent(duplicate)
            store.close()


class ApiProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sendgrid_request_and_message_id(self) -> None:
        client = FakeClient(FakeResponse(headers={"X-Message-Id": "sg-123"}))
        environment = {
            "SENDGRID_API_KEY": "secret",
            "SENDGRID_API_BASE": "https://api.sendgrid.com",
        }
        with patch.dict(os.environ, environment), patch(
            "httpx.AsyncClient", return_value=client
        ):
            result = await SendGridProvider(ServiceConfig(simulate=False)).send(message())

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.message_id, "sg-123")
        url, request = client.calls[0]
        self.assertEqual(url, "https://api.sendgrid.com/v3/mail/send")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(request["json"]["content"][0]["type"], "text/html")
        self.assertEqual(
            request["json"]["personalizations"][0]["headers"]["In-Reply-To"],
            "<parent@example.org>",
        )

    async def test_mailgun_request_supports_eu_base_url(self) -> None:
        client = FakeClient(FakeResponse({"id": "<mg-123@example.org>"}))
        environment = {
            "MAILGUN_API_KEY": "secret",
            "MAILGUN_DOMAIN": "mg.example.org",
            "MAILGUN_API_BASE": "https://api.eu.mailgun.net/v3/",
        }
        with patch.dict(os.environ, environment), patch(
            "httpx.AsyncClient", return_value=client
        ):
            result = await MailgunProvider(ServiceConfig(simulate=False)).send(message("Hello"))

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.message_id, "<mg-123@example.org>")
        url, request = client.calls[0]
        self.assertEqual(url, "https://api.eu.mailgun.net/v3/mg.example.org/messages")
        self.assertEqual(request["auth"], ("api", "secret"))
        self.assertEqual(request["data"]["text"], "Hello")
        self.assertEqual(request["data"]["h:Reply-To"], "reply@example.org")

    async def test_postmark_request_and_rejection(self) -> None:
        success_client = FakeClient(FakeResponse({"ErrorCode": 0, "MessageID": "pm-123"}))
        environment = {
            "POSTMARK_SERVER_TOKEN": "secret",
            "POSTMARK_MESSAGE_STREAM": "broadcasts",
        }
        with patch.dict(os.environ, environment), patch(
            "httpx.AsyncClient", return_value=success_client
        ):
            result = await PostmarkProvider(ServiceConfig(simulate=False)).send(message())

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.message_id, "pm-123")
        _, request = success_client.calls[0]
        self.assertEqual(request["headers"]["X-Postmark-Server-Token"], "secret")
        self.assertEqual(request["json"]["MessageStream"], "broadcasts")
        self.assertEqual(request["json"]["HtmlBody"], "<p>Hello</p>")

        rejected_client = FakeClient(FakeResponse({"ErrorCode": 406, "Message": "Inactive"}))
        with patch.dict(os.environ, {"POSTMARK_SERVER_TOKEN": "secret"}), patch(
            "httpx.AsyncClient", return_value=rejected_client
        ):
            rejected = await PostmarkProvider(ServiceConfig(simulate=False)).send(message())

        self.assertFalse(rejected.success)
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(rejected.error, "Inactive")

    async def test_resend_request_and_message_id(self) -> None:
        client = FakeClient(FakeResponse({"id": "re-123"}))
        with patch.dict(os.environ, {"RESEND_API_KEY": "secret"}), patch(
            "httpx.AsyncClient", return_value=client
        ):
            result = await ResendProvider(ServiceConfig(simulate=False)).send(message())

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.message_id, "re-123")
        url, request = client.calls[0]
        self.assertEqual(url, "https://api.resend.com/emails")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(request["json"]["to"], ["lead@example.com"])
        self.assertEqual(request["json"]["headers"]["References"], message().references)

    async def test_missing_credentials_are_normalized(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = await ResendProvider(ServiceConfig(simulate=False)).send(message())

        self.assertFalse(result.success)
        self.assertEqual(result.provider, "resend")
        self.assertEqual(result.status, "error")
        self.assertIn("RESEND_API_KEY", result.error)


if __name__ == "__main__":
    unittest.main()
