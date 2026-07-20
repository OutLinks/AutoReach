"""
Tracking Layer orchestrator.

Bundles the four trackers and exposes the two operations the rest of the agent
needs:

  - instrument(): inject the open pixel + wrap links into a body *before* sending,
  - the individual trackers, for the tracking server / webhooks to call when
    delivery, open, click, or reply events arrive.

It also rolls up aggregate counts (used by the reputation layer) so callers have
a single place to read engagement metrics.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...storage.send_store import SendStore
from .delivery_tracker import DeliveryTracker
from .open_tracker import OpenTracker
from .click_tracker import ClickTracker
from .reply_detector import ReplyDetector, ReplyNotification

logger = logging.getLogger(__name__)


class TrackingLayer:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store
        self.delivery = DeliveryTracker(store)
        self.open = OpenTracker(config, store)
        self.click = ClickTracker(config, store)
        self.reply = ReplyDetector(store, handoff_enabled=config.reply_handoff_enabled)

    def instrument(self, body: str, sent_email_id: str) -> str:
        """Wrap links then inject the open pixel for a sent email's body."""
        body = self.click.wrap_links(body, sent_email_id)
        body = self.open.inject_pixel(body, sent_email_id)
        return body

    def on_send_accepted(self, sent_email_id: str, lead_id: str = "") -> None:
        """Called right after a provider accepts a send."""
        self.delivery.mark_delivered(sent_email_id, lead_id)

    def record_reply(self, sent_email_id: str, snippet: str = "") -> ReplyNotification | None:
        return self.reply.record_reply(sent_email_id, snippet)

    def engagement_summary(self) -> dict[str, int]:
        return self._store.count_events_by_type()
