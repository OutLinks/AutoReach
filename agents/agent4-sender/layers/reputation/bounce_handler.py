"""
Bounce handler (Reputation Layer #1).

Classifies bounces and reacts:
  - hard  → address is invalid; suppress it, stop the lead's sequence,
  - soft  → temporary (full mailbox, greylisting); retry up to N times, then
            treat as hard,
  - block → recipient server rejected for reputation reasons; suppress and flag
            the sending account for review.

Records a "bounce" tracking event and flips the SentEmail to bounced so the
sender-score monitor sees it in the aggregate counts.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ...config import ServiceConfig
from ...models import SuppressionEntry, TrackingEvent
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)


class BounceHandler:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store
        self._soft_retries: dict[str, int] = defaultdict(int)

    def handle(
        self,
        sent_email_id: str,
        bounce_type: str = "hard",
        detail: str = "",
    ) -> str:
        """
        Process a bounce. Returns the effective disposition:
        "suppressed" | "retry" | "ignored".
        """
        sent = self._store.get_sent(sent_email_id)
        if not sent:
            logger.warning("BounceHandler: unknown sent_email_id %s", sent_email_id)
            return "ignored"

        recipient = sent.get("recipient", "")
        lead_id = sent.get("lead_id", "")

        self._store.insert_event(
            TrackingEvent(
                sent_email_id=sent_email_id,
                lead_id=lead_id,
                event_type="bounce",
                bounce_type=bounce_type,
                detail=detail,
            )
        )

        if bounce_type == "soft":
            self._soft_retries[recipient] += 1
            if self._soft_retries[recipient] <= self._config.soft_bounce_max_retries:
                logger.info(
                    "BounceHandler: soft bounce %s (retry %d/%d)",
                    recipient, self._soft_retries[recipient],
                    self._config.soft_bounce_max_retries,
                )
                return "retry"
            logger.info("BounceHandler: soft bounce exhausted retries → treating as hard")

        # hard, block, or exhausted soft → suppress.
        self._store.update_sent_status(sent_email_id, "bounced", bounced=True)
        self._store.add_suppression(
            SuppressionEntry(
                value=recipient,
                reason="hard_bounce",
                detail=f"{bounce_type}: {detail}"[:200],
            )
        )
        logger.info("BounceHandler: %s bounce → suppressed %s", bounce_type, recipient)
        return "suppressed"
