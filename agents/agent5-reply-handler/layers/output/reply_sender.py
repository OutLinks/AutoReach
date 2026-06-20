"""
Reply sender (Output Layer — send email).

Sends the drafted response back to the lead, threaded onto the original message.
Supports SMTP and the Gmail API; in simulate mode it doesn't touch the network —
it logs and returns a synthetic message id so the pipeline runs without
credentials (mirrors Agent 4's sending layer).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import smtplib
import uuid
from email.message import EmailMessage

from ...config import ServiceConfig

logger = logging.getLogger(__name__)


class ReplySender:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        in_reply_to: str = "",
    ) -> tuple[bool, str]:
        """Return (success, message_id)."""
        if self._config.simulate or not to_email:
            mid = f"<{uuid.uuid4().hex}@agent5.simulated>"
            logger.info("ReplySender[sim]: reply to %s (subject=%r)", to_email, subject[:40])
            return True, mid

        try:
            if self._config.send_provider == "gmail":
                return await self._send_gmail(to_email, subject, body, in_reply_to)
            return await self._send_smtp(to_email, subject, body, in_reply_to)
        except Exception as exc:
            logger.error("ReplySender: send failed — %s", exc)
            return False, ""

    # ── SMTP ───────────────────────────────────────────────────────────────────

    async def _send_smtp(self, to_email, subject, body, in_reply_to) -> tuple[bool, str]:
        host = os.getenv("SMTP_HOST", "")
        if not host:
            raise RuntimeError("SMTP_HOST is not set")
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "")
        password = os.getenv("SMTP_PASSWORD", "")

        sender = self._config.sender_email or username
        message_id = f"<{uuid.uuid4().hex}@{sender.split('@')[-1]}>"
        mime = self._build_mime(sender, to_email, subject, body, in_reply_to, message_id)
        await asyncio.to_thread(self._deliver_smtp, host, port, username, password, mime)
        return True, message_id

    @staticmethod
    def _deliver_smtp(host, port, username, password, mime) -> None:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if server.has_extn("STARTTLS"):
                server.starttls()
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(mime)

    # ── Gmail ──────────────────────────────────────────────────────────────────

    async def _send_gmail(self, to_email, subject, body, in_reply_to) -> tuple[bool, str]:
        token = os.getenv("GMAIL_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("GMAIL_ACCESS_TOKEN is not set")
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Gmail sender") from exc

        sender = self._config.sender_email
        mime = self._build_mime(sender, to_email, subject, body, in_reply_to, "")
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw},
            )
            resp.raise_for_status()
            data = resp.json()
        return True, data.get("id", "")

    @staticmethod
    def _build_mime(sender, to_email, subject, body, in_reply_to, message_id) -> EmailMessage:
        mime = EmailMessage()
        if message_id:
            mime["Message-ID"] = message_id
        mime["To"] = to_email
        mime["From"] = sender
        mime["Subject"] = subject
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to
            mime["References"] = in_reply_to
        mime.set_content(body)
        return mime
