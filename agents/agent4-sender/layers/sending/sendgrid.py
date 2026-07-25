"""Twilio SendGrid Web API sending provider."""

from __future__ import annotations

import os

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

class SendGridProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "sendgrid"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        api_key = os.getenv("SENDGRID_API_KEY", "")
        if not api_key:
            raise RuntimeError("SENDGRID_API_KEY is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the SendGrid provider") from exc

        personalization: dict[str, object] = {
            "to": [{"email": message.to_email}],
        }
        headers = _thread_headers(message)
        if headers:
            personalization["headers"] = headers

        content_type = "text/html" if _looks_like_html(message.body) else "text/plain"
        api_base = os.getenv("SENDGRID_API_BASE", "https://api.sendgrid.com").rstrip("/")
        payload: dict[str, object] = {
            "personalizations": [personalization],
            "from": {"email": message.from_email, "name": message.from_name},
            "subject": message.subject,
            "content": [{"type": content_type, "value": message.body}],
        }
        if message.reply_to:
            payload["reply_to"] = {"email": message.reply_to}

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{api_base}/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()

        return SendResult(
            success=True,
            provider=self.name,
            message_id=response.headers.get("X-Message-Id", ""),
            status="sent",
        )


def _looks_like_html(body: str) -> bool:
    return "<" in body and ">" in body


def _thread_headers(message: OutgoingMessage) -> dict[str, str]:
    headers: dict[str, str] = {}
    if message.in_reply_to:
        headers["In-Reply-To"] = message.in_reply_to
    if message.references:
        headers["References"] = message.references
    return headers
