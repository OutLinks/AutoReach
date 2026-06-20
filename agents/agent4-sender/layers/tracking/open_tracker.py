"""
Open tracker (Tracking Layer #2).

Embeds a 1x1 tracking pixel keyed by the SentEmail id. When the recipient's
client loads the pixel, the tracking server (out of scope here) calls
`record_open`, which logs an "open" event and flips the SentEmail.opened flag.
Open rate is a primary signal for the reputation layer and for A/B testing
subject lines.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import TrackingEvent
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)


class OpenTracker:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store

    def pixel_url(self, sent_email_id: str) -> str:
        return f"{self._config.tracking_pixel_base_url}/o/{sent_email_id}.gif"

    def inject_pixel(self, body: str, sent_email_id: str) -> str:
        """Append an invisible tracking pixel to an (HTML) email body."""
        if not self._config.track_opens:
            return body
        pixel = (
            f'<img src="{self.pixel_url(sent_email_id)}" width="1" height="1" '
            f'alt="" style="display:none" />'
        )
        return f"{body}\n{pixel}"

    def record_open(self, sent_email_id: str, lead_id: str = "", user_agent: str = "") -> None:
        self._store.insert_event(
            TrackingEvent(
                sent_email_id=sent_email_id,
                lead_id=lead_id,
                event_type="open",
                metadata={"user_agent": user_agent} if user_agent else {},
            )
        )
        self._store.update_sent_status(sent_email_id, "opened", opened=True)
        logger.debug("OpenTracker: %s opened", sent_email_id)
