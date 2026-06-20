"""
Outreach.io sending provider (Sending Layer #3).

Outreach is a sales-engagement platform with a JSON:API. Real delivery there is
typically modeled as creating a mailing on a prospect/sequence; here we expose a
thin send adapter that posts a mailing and normalizes the result. Requires
OUTREACH_ACCESS_TOKEN when not simulating.
"""

from __future__ import annotations

import logging
import os

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

logger = logging.getLogger(__name__)

_API_BASE = "https://api.outreach.io/api/v2"


class OutreachProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "outreach"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        token = os.getenv("OUTREACH_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("OUTREACH_ACCESS_TOKEN is not set")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for the Outreach provider") from exc

        body = {
            "data": {
                "type": "mailing",
                "attributes": {
                    "subject": message.subject,
                    "bodyHtml": message.body,
                    "mailboxAddress": message.from_email,
                    "toRecipients": [message.to_email],
                },
            }
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_API_BASE}/mailings",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/vnd.api+json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        message_id = str(((data.get("data") or {}).get("id")) or "")
        return SendResult(
            success=True, provider=self.name, message_id=message_id, status="sent"
        )
