"""
Delivery tracker (Tracking Layer #1).

Confirms a message reached the recipient's mail server. Right after a provider
accepts a send we record a "delivered" event and advance the SentEmail from
"sent" → "delivered". A bounce arriving later (via the reputation layer) reverses
that. In simulation mode delivery is assumed; with a real provider this is driven
by SMTP responses / provider webhooks.
"""

from __future__ import annotations

import logging

from ...models import TrackingEvent
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)


class DeliveryTracker:
    def __init__(self, store: SendStore) -> None:
        self._store = store

    def mark_delivered(self, sent_email_id: str, lead_id: str = "", detail: str = "") -> None:
        self._store.insert_event(
            TrackingEvent(
                sent_email_id=sent_email_id,
                lead_id=lead_id,
                event_type="delivered",
                detail=detail,
            )
        )
        self._store.update_sent_status(sent_email_id, "delivered")
        logger.debug("DeliveryTracker: %s delivered", sent_email_id)
