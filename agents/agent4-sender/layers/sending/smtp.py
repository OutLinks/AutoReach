"""
Custom SMTP sending provider (Sending Layer #4).

Maximum control: deliver straight through your own mail server. The send runs in
a worker thread (smtplib is blocking) so it doesn't stall the async pipeline.
Reads SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD when not simulating.
Reputation is entirely on you, so pair this with the reputation layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import uuid
from email.message import EmailMessage

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

logger = logging.getLogger(__name__)


class SmtpProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "smtp"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        host = os.getenv("SMTP_HOST", "")
        if not host:
            raise RuntimeError("SMTP_HOST is not set")
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "")
        password = os.getenv("SMTP_PASSWORD", "")

        domain = message.from_email.split("@")[-1]
        message_id = f"<{uuid.uuid4().hex}@{domain}>"
        mime = self._build_mime(message, message_id)

        # smtplib is blocking — push it to a thread.
        await asyncio.to_thread(self._deliver, host, port, username, password, mime)
        return SendResult(
            success=True, provider=self.name, message_id=message_id, status="sent"
        )

    @staticmethod
    def _deliver(host, port, username, password, mime: EmailMessage) -> None:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if server.has_extn("STARTTLS"):
                server.starttls()
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(mime)

    @staticmethod
    def _build_mime(message: OutgoingMessage, message_id: str) -> EmailMessage:
        mime = EmailMessage()
        mime["Message-ID"] = message_id
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
        return mime
