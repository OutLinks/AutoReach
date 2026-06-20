"""
Abstract base for sending providers.

Every provider takes an OutgoingMessage and returns a normalized SendResult, so
the rest of Agent 4 never branches on which platform actually delivered the mail
(mirrors how core.model_selection abstracts LLM providers).

When `config.simulate` is True, providers short-circuit to a simulated success
so the full pipeline runs without any real credentials or network calls.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...config import ServiceConfig
from ...models import SendResult

logger = logging.getLogger(__name__)


@dataclass
class OutgoingMessage:
    """A single email handed to a provider for delivery."""

    to_email: str
    from_email: str
    from_name: str
    subject: str
    body: str
    reply_to: str = ""
    # Threading headers for follow-ups (so they land in the same thread).
    in_reply_to: str = ""
    references: str = ""


class SendingProvider(ABC):
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical provider identifier, e.g. 'instantly'."""
        ...

    async def send(self, message: OutgoingMessage) -> SendResult:
        """Deliver one message, honoring simulation mode."""
        if self._config.simulate:
            return self._simulated(message)
        try:
            return await self._send_real(message)
        except Exception as exc:  # never let a provider error crash the batch
            logger.error("%s: send failed — %s", self.name, exc)
            return SendResult(success=False, provider=self.name, status="error", error=str(exc))

    @abstractmethod
    async def _send_real(self, message: OutgoingMessage) -> SendResult:
        """Provider-specific real delivery. Only called when not simulating."""
        ...

    def _simulated(self, message: OutgoingMessage) -> SendResult:
        message_id = f"<{uuid.uuid4().hex}@{self.name}.simulated>"
        logger.info(
            "%s[sim]: delivered to %s (subject=%r)",
            self.name, message.to_email, message.subject[:40],
        )
        return SendResult(
            success=True, provider=self.name, message_id=message_id, status="sent"
        )
