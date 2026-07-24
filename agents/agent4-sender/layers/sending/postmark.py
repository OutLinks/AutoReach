"""Postmark Email API sending provider."""

from __future__ import annotations

import os
from email.utils import formataddr

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

_SEND_URL = "https://api.postmarkapp.com/email"


class PostmarkProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "postmark"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        token = os.getenv("POSTMARK_SERVER_TOKEN", "")
        if not token:
            raise RuntimeError("POSTMARK_SERVER_TOKEN is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Postmark provider") from exc

        payload: dict[str, object] = {
            "From": formataddr((message.from_name, message.from_email)),
            "To": message.to_email,
            "Subject": message.subject,
            "MessageStream": os.getenv("POSTMARK_MESSAGE_STREAM", "outbound"),
        }
        payload["HtmlBody" if _looks_like_html(message.body) else "TextBody"] = message.body
        if message.reply_to:
            payload["ReplyTo"] = message.reply_to

        headers = []
        if message.in_reply_to:
            headers.append({"Name": "In-Reply-To", "Value": message.in_reply_to})
        if message.references:
            headers.append({"Name": "References", "Value": message.references})
        if headers:
            payload["Headers"] = headers

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _SEND_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": token,
                },
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

        error_code = int(result.get("ErrorCode", 0))
        if error_code:
            return SendResult(
                success=False,
                provider=self.name,
                status="rejected",
                error=result.get("Message", f"Postmark error {error_code}"),
            )
        return SendResult(
            success=True,
            provider=self.name,
            message_id=result.get("MessageID", ""),
            status="sent",
        )


def _looks_like_html(body: str) -> bool:
    return "<" in body and ">" in body
