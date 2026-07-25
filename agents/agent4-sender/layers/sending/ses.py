"""
AWS SES sending provider (Sending Layer).

Sends mail through Amazon Simple Email Service using boto3. Requires AWS
credentials configured through the standard AWS SDK chain and a region from
AWS_REGION, AWS_DEFAULT_REGION, or AWS_SES_REGION when not simulating. The sender
address must be verified in SES, or the account must be out of the SES sandbox.
"""

from __future__ import annotations

import asyncio
import logging
import os
from email.message import EmailMessage

from ...models import SendResult
from .base import OutgoingMessage, SendingProvider

logger = logging.getLogger(__name__)


class SesProvider(SendingProvider):
    @property
    def name(self) -> str:
        return "ses"

    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        region = (
            os.getenv("AWS_SES_REGION")
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
        )
        if not region:
            raise RuntimeError("AWS_SES_REGION, AWS_REGION, or AWS_DEFAULT_REGION is not set")

        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for the SES provider") from exc

        client = boto3.client("ses", region_name=region)
        mime = self._build_mime(message)

        response = await asyncio.to_thread(
            client.send_raw_email,
            Source=message.from_email,
            Destinations=[message.to_email],
            RawMessage={"Data": mime.as_bytes()},
        )

        return SendResult(
            success=True,
            provider=self.name,
            message_id=response.get("MessageId", ""),
            status="sent",
        )

    @staticmethod
    def _build_mime(message: OutgoingMessage) -> EmailMessage:
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

        body = message.body or ""
        if "<" in body and ">" in body:
            mime.set_content("This message contains HTML content.")
            mime.add_alternative(body, subtype="html")
        else:
            mime.set_content(body)
        return mime
