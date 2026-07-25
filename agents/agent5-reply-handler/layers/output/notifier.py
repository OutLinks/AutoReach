"""
Notifier (Output Layer — send notifications).

Surfaces things a human must see: hand-offs, booked meetings, and alerts. Each
notification is persisted to the conversation store and written as a JSON file in
output/notifications/ (a simple, dependency-free inbox). A real deployment would
also POST to Slack/email here — that goes behind `_dispatch_external`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.runtime_paths import agent_output_dir

from ...models import Notification
from ...storage.conversation_store import ConversationStore

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, store: ConversationStore, notifications_dir: Path | None = None) -> None:
        self._store = store
        self._dir = notifications_dir or agent_output_dir("agent5-reply-handler") / "notifications"

    def notify(self, notification: Notification) -> None:
        self._store.add_notification(notification)
        self._write_file(notification)
        self._dispatch_external(notification)
        logger.info(
            "Notifier: [%s] %s — %s",
            notification.urgency, notification.kind, notification.title,
        )

    def handoff(self, lead_id: str, reason: str, urgency: str, summary: str) -> Notification:
        n = Notification(
            kind="handoff",
            lead_id=lead_id,
            title=f"Human review needed: lead {lead_id}",
            message=f"{summary}\nReason: {reason}",
            urgency=urgency,
        )
        self.notify(n)
        return n

    def meeting_booked(self, lead_id: str, summary: str = "") -> Notification:
        n = Notification(
            kind="meeting_booked",
            lead_id=lead_id,
            title=f"Meeting booked with lead {lead_id}",
            message=summary or "A meeting was booked — follow-ups stopped.",
            urgency="high",
        )
        self.notify(n)
        return n

    # ── Internal ───────────────────────────────────────────────────────────────

    def _write_file(self, notification: Notification) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{notification.kind}_{notification.lead_id}_{notification.id[:8]}.json"
            path.write_text(json.dumps(json.loads(notification.model_dump_json()), indent=2))
        except OSError as exc:
            logger.error("Notifier: could not write notification file — %s", exc)

    def _dispatch_external(self, notification: Notification) -> None:
        """Hook for Slack/email/webhook delivery (no-op by default)."""
        return None
