"""Mailgun Messages API sending provider."""

from __future__ import annotations

import os
from email.utils import formataddr

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider


class MailgunProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "mailgun"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        api_key = os.getenv("MAILGUN_API_KEY", "")
        domain = os.getenv("MAILGUN_DOMAIN", "")
        if not api_key:
            raise RuntimeError("MAILGUN_API_KEY is not set")
        if not domain:
            raise RuntimeError("MAILGUN_DOMAIN is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Mailgun provider") from exc

        api_base = os.getenv("MAILGUN_API_BASE", "https://api.mailgun.net/v3").rstrip("/")
        data = {
            "from": formataddr((message.from_name, message.from_email)),
            "to": message.to_email,
            "subject": message.subject,
            "html" if _looks_like_html(message.body) else "text": message.body,
        }
        if message.reply_to:
            data["h:Reply-To"] = message.reply_to
        if message.in_reply_to:
            data["h:In-Reply-To"] = message.in_reply_to
        if message.references:
            data["h:References"] = message.references

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{api_base}/{domain}/messages",
                auth=("api", api_key),
                data=data,
            )
            response.raise_for_status()
            result = response.json()

        return SendResult(
            success=True,
            provider=self.name,
            message_id=result.get("id", ""),
            status="sent",
        )


def _looks_like_html(body: str) -> bool:
    return "<" in body and ">" in body
