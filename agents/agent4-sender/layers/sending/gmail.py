"""
Gmail API sending provider (Sending Layer #2).

Sends through the authenticated user's Gmail mailbox via the Gmail REST API
(users.messages.send) with a base64url-encoded RFC 822 message. Requires
GMAIL_ACCESS_TOKEN (an OAuth2 bearer token with gmail.send scope) when not
simulating. Lower volume than Instantly; relies on the domain's SPF/DKIM/DMARC.
"""

from __future__ import annotations

import base64
import logging
import os
from email.message import EmailMessage

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

logger = logging.getLogger(__name__)

_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "gmail"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        token = os.getenv("GMAIL_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("GMAIL_ACCESS_TOKEN is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Gmail provider") from exc

        raw = base64.urlsafe_b64encode(self._build_mime(message)).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _SEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw},
            )
            resp.raise_for_status()
            data = resp.json()

        # Gmail returns its own message id; prefer the RFC822 Message-ID header.
        return SendResult(
            success=True,
            provider=self.name,
            message_id=data.get("id", ""),
            status="sent",
        )

    @staticmethod
    def _build_mime(message: OutgoingMessage) -> bytes:
        mime = EmailMessage()
        mime["To"] = message.to_email
        mime["From"] = f"{message.from_name} <{message.from_email}>"
        mime["Subject"] = message.subject
        if message.reply_to:
            mime["Reply-To"] = message.reply_to
        if message.in_reply_to:
            mime["In-Reply-To"] = message.in_reply_to
        if message.references:
            mime["References"] = message.references
        mime.set_content(message.body)
        return mime.as_bytes()
