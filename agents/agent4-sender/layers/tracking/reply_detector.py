"""
Reply detector (Tracking Layer #4).

The most important tracking signal: a reply means the lead engaged, so the
follow-up sequence must stop immediately and Agent 5 (Reply Handler) takes over.

A reply can arrive via an Instantly webhook, Gmail API polling, or IMAP IDLE. All
paths converge on `record_reply`, which:
  - logs a "reply" event,
  - flips SentEmail.replied + status,
  - returns a ReplyNotification the agent forwards to Agent 5 and uses to pause
    the lead's sequence.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from ...models import TrackingEvent
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)


class ReplyNotification(BaseModel):
    """Hand-off payload written for Agent 5 when a reply is detected."""

    lead_id: str
    sent_email_id: str
    email_id: str = ""
    message_id: str = ""
    recipient: str = ""
    snippet: str = ""
    detected_at: datetime = datetime.utcnow()


class ReplyDetector:
    def __init__(self, store: SendStore, handoff_dir: Path | None = None) -> None:
        self._store = store
        # Where Agent 5 picks up reply notifications (file drop = loose coupling).
        self._handoff_dir = handoff_dir or (
            Path(__file__).parent.parent.parent / "output" / "replies"
        )

    def record_reply(
        self,
        sent_email_id: str,
        snippet: str = "",
        lead_id: str = "",
    ) -> ReplyNotification | None:
        sent = self._store.get_sent(sent_email_id)
        if not sent:
            logger.warning("ReplyDetector: unknown sent_email_id %s", sent_email_id)
            return None

        lead_id = lead_id or sent.get("lead_id", "")
        self._store.insert_event(
            TrackingEvent(
                sent_email_id=sent_email_id,
                lead_id=lead_id,
                event_type="reply",
                detail=snippet[:280],
            )
        )
        self._store.update_sent_status(sent_email_id, "replied", replied=True)

        notification = ReplyNotification(
            lead_id=lead_id,
            sent_email_id=sent_email_id,
            email_id=sent.get("email_id", ""),
            message_id=sent.get("message_id", ""),
            recipient=sent.get("recipient", ""),
            snippet=snippet[:280],
        )
        self._write_handoff(notification)
        logger.info("ReplyDetector: reply from lead %s → notifying Agent 5", lead_id)
        return notification

    def _write_handoff(self, notification: ReplyNotification) -> None:
        try:
            self._handoff_dir.mkdir(parents=True, exist_ok=True)
            path = self._handoff_dir / f"reply_{notification.lead_id}.json"
            path.write_text(json.dumps(json.loads(notification.model_dump_json()), indent=2))
        except OSError as exc:
            logger.error("ReplyDetector: could not write hand-off — %s", exc)
