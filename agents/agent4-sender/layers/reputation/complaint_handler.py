"""
Complaint handler (Reputation Layer #2).

A spam complaint (via a feedback loop) is the most damaging signal there is. We
react hard and immediately:
  - mark the SentEmail as complained,
  - permanently suppress the address (never email again),
  - stop the lead's sequence.

The sender-score monitor decides whether the resulting complaint *rate* warrants
pausing the account entirely.
"""

from __future__ import annotations

import logging

from ...models import SuppressionEntry, TrackingEvent
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)


class ComplaintHandler:
    def __init__(self, store: SendStore) -> None:
        self._store = store

    def handle(self, sent_email_id: str, detail: str = "") -> bool:
        """Process a complaint. Returns True if it was recorded."""
        sent = self._store.get_sent(sent_email_id)
        if not sent:
            logger.warning("ComplaintHandler: unknown sent_email_id %s", sent_email_id)
            return False

        recipient = sent.get("recipient", "")
        lead_id = sent.get("lead_id", "")

        self._store.insert_event(
            TrackingEvent(
                sent_email_id=sent_email_id,
                lead_id=lead_id,
                event_type="complaint",
                detail=detail,
            )
        )
        self._store.update_sent_status(sent_email_id, "complained")
        self._store.add_suppression(
            SuppressionEntry(
                value=recipient,
                reason="complaint",
                detail=detail[:200],
            )
        )
        logger.warning("ComplaintHandler: complaint from %s → suppressed", recipient)
        return True
