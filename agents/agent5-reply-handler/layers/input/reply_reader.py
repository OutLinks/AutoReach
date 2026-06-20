"""
Reply reader (Input Layer #1).

Reads the reply hand-off files Agent 4 drops in its output/replies/ directory.
Each file is a ReplyNotification JSON:
    {lead_id, sent_email_id, email_id, message_id, recipient, snippet, detected_at}

`snippet` is the lead's reply text. Consumed files are renamed with a `.done`
suffix so the same reply isn't handled twice. Callers can also inject replies
directly (e.g. from a webhook) via `from_payload`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ...models import IncomingReply

logger = logging.getLogger(__name__)


class ReplyReader:
    def __init__(self, replies_dir: str) -> None:
        self._dir = Path(replies_dir)

    def read_pending(self, mark_done: bool = True) -> list[IncomingReply]:
        """Load all un-handled reply hand-offs from Agent 4."""
        if not self._dir.exists():
            logger.info("ReplyReader: no replies dir at %s", self._dir)
            return []

        replies: list[IncomingReply] = []
        for path in sorted(self._dir.glob("reply_*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("ReplyReader: bad hand-off file %s — %s", path, exc)
                continue

            replies.append(self.from_payload(payload))
            if mark_done:
                try:
                    path.rename(path.with_suffix(".json.done"))
                except OSError:
                    logger.warning("ReplyReader: could not mark %s done", path)

        logger.info("ReplyReader: loaded %d pending replies", len(replies))
        return replies

    @staticmethod
    def from_payload(payload: dict) -> IncomingReply:
        return IncomingReply(
            lead_id=payload.get("lead_id", ""),
            conversation_id=payload.get("lead_id", ""),
            sent_email_id=payload.get("sent_email_id", ""),
            email_id=payload.get("email_id", ""),
            message_id=payload.get("message_id", ""),
            from_email=payload.get("recipient", ""),
            raw_body=payload.get("snippet", "") or payload.get("body", ""),
        )
