"""Resend Email API sending provider."""

from __future__ import annotations

import os
from email.utils import formataddr

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

_SEND_URL = "https://api.resend.com/emails"


class ResendProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "resend"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        api_key = os.getenv("RESEND_API_KEY", "")
        if not api_key:
            raise RuntimeError("RESEND_API_KEY is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Resend provider") from exc

        payload: dict[str, object] = {
            "from": formataddr((message.from_name, message.from_email)),
            "to": [message.to_email],
            "subject": message.subject,
            "html" if _looks_like_html(message.body) else "text": message.body,
        }
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        headers = {}
        if message.in_reply_to:
            headers["In-Reply-To"] = message.in_reply_to
        if message.references:
            headers["References"] = message.references
        if headers:
            payload["headers"] = headers

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _SEND_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
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
