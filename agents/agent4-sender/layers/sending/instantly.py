"""
Instantly.ai sending provider (Sending Layer #1).

Instantly is purpose-built for cold email — it manages deliverability, warm-up,
and tracking, exposing a REST API. We POST the message to its send endpoint and
normalize the response. Requires INSTANTLY_API_KEY when not simulating.
"""

from __future__ import annotations

import logging
import os

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

logger = logging.getLogger(__name__)

_API_BASE = "https://api.instantly.ai/api/v1"


class InstantlyProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "instantly"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        api_key = os.getenv("INSTANTLY_API_KEY", "")
        if not api_key:
            raise RuntimeError("INSTANTLY_API_KEY is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Instantly provider") from exc

        payload = {
            "api_key": api_key,
            "to": message.to_email,
            "from": message.from_email,
            "from_name": message.from_name,
            "subject": message.subject,
            "body": message.body,
            "reply_to": message.reply_to or message.from_email,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_API_BASE}/send", json=payload)
            resp.raise_for_status()
            data = resp.json()

        message_id = data.get("message_id") or data.get("id", "")
        return SendResult(
            success=True, provider=self.name, message_id=message_id, status="sent"
        )
