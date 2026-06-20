"""
Click tracker (Tracking Layer #3).

Rewrites links in the body to pass through a redirect endpoint that logs the
click before forwarding to the real destination. When the redirect fires, the
tracking server calls `record_click`. Clicks measure how compelling the content
(not just the subject) is.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from ...config import ServiceConfig
from ...models import TrackingEvent
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


class ClickTracker:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store

    def redirect_url(self, sent_email_id: str, target: str) -> str:
        base = self._config.tracking_pixel_base_url
        return f"{base}/c/{sent_email_id}?u={quote(target, safe='')}"

    def wrap_links(self, body: str, sent_email_id: str) -> str:
        """Rewrite every href in an HTML body to go through the click redirect."""
        if not self._config.track_clicks:
            return body

        def _replace(match: re.Match) -> str:
            target = match.group(1)
            return f'href="{self.redirect_url(sent_email_id, target)}"'

        return _URL_RE.sub(_replace, body)

    def record_click(self, sent_email_id: str, url: str, lead_id: str = "") -> None:
        self._store.insert_event(
            TrackingEvent(
                sent_email_id=sent_email_id,
                lead_id=lead_id,
                event_type="click",
                detail=url,
            )
        )
        self._store.update_sent_status(sent_email_id, "clicked", clicked=True)
        logger.debug("ClickTracker: %s clicked %s", sent_email_id, url)
