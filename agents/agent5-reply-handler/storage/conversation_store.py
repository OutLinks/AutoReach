"""
Conversation store — SQLite-backed memory for Agent 5.

Tables:
  conversations   — one row per lead conversation (status, last intent/sentiment)
  messages        — every inbound/outbound message, with intent + action metadata
  handoffs        — escalation packages handed to humans
  notifications   — human-facing alerts (handoff, meeting booked, etc.)
  reply_jobs      — one row per batch run

Conversation.id == lead_id, giving a stable thread key across runs and the FK
chain back through Agents 1–4.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import (
    Conversation,
    ConversationMessage,
    HandoffPackage,
    Notification,
    ReplyJob,
)

logger = logging.getLogger(__name__)

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id             TEXT PRIMARY KEY,
        lead_id        TEXT,
        recipient      TEXT,
        status         TEXT DEFAULT 'active',
        message_count  INTEGER DEFAULT 0,
        last_intent    TEXT,
        last_sentiment TEXT,
        escalated      INTEGER DEFAULT 0,
        created_at     TEXT,
        updated_at     TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT,
        lead_id         TEXT,
        direction       TEXT,
        body            TEXT,
        message_id      TEXT,
        intent          TEXT,
        sentiment       TEXT,
        action_taken    TEXT,
        created_at      TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS handoffs (
        id                   TEXT PRIMARY KEY,
        lead_id              TEXT,
        reason               TEXT,
        urgency              TEXT,
        summary              TEXT,
        suggested_response   TEXT,
        conversation_excerpt TEXT,
        created_at           TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id         TEXT PRIMARY KEY,
        kind       TEXT,
        lead_id    TEXT,
        title      TEXT,
        message    TEXT,
        urgency    TEXT,
        created_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS reply_jobs (
        id              TEXT PRIMARY KEY,
        status          TEXT,
        total           INTEGER,
        handled         INTEGER,
        escalated       INTEGER,
        replies_sent    INTEGER,
        meetings_booked INTEGER,
        skipped         INTEGER,
        created_at      TEXT,
        completed_at    TEXT
    );
    """,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages (conversation_id);",
    "CREATE INDEX IF NOT EXISTS idx_msg_lead ON messages (lead_id);",
    "CREATE INDEX IF NOT EXISTS idx_handoff_lead ON handoffs (lead_id);",
]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


class ConversationStore:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            for idx in _INDEXES:
                self._conn.execute(idx)
        logger.info("ConversationStore: opened at %s", db_path)

    # ── Conversations ──────────────────────────────────────────────────────────

    def get_conversation(self, lead_id: str) -> Optional[Conversation]:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (lead_id,)
        ).fetchone()
        if not row:
            return None
        return Conversation(
            id=row["id"], lead_id=row["lead_id"], recipient=row["recipient"] or "",
            status=row["status"], message_count=row["message_count"] or 0,
            last_intent=row["last_intent"] or "", last_sentiment=row["last_sentiment"] or "",
            escalated=bool(row["escalated"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.utcnow(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.utcnow(),
        )

    def upsert_conversation(self, c: Conversation) -> None:
        c.updated_at = datetime.utcnow()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO conversations (
                    id, lead_id, recipient, status, message_count, last_intent,
                    last_sentiment, escalated, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    c.id, c.lead_id, c.recipient, c.status, c.message_count,
                    c.last_intent, c.last_sentiment, int(c.escalated),
                    _iso(c.created_at), _iso(c.updated_at),
                ),
            )

    def count_exchanges(self, lead_id: str) -> int:
        """Number of inbound (lead → us) messages recorded so far."""
        row = self._conn.execute(
            "SELECT COUNT(*) n FROM messages WHERE lead_id = ? AND direction = 'inbound'",
            (lead_id,),
        ).fetchone()
        return row["n"] or 0

    # ── Messages ───────────────────────────────────────────────────────────────

    def add_message(self, m: ConversationMessage) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO messages (
                    id, conversation_id, lead_id, direction, body, message_id,
                    intent, sentiment, action_taken, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    m.id, m.conversation_id, m.lead_id, m.direction, m.body,
                    m.message_id, m.intent, m.sentiment, m.action_taken,
                    _iso(m.created_at),
                ),
            )

    def get_messages(self, lead_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE lead_id = ? ORDER BY created_at",
            (lead_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Handoffs + notifications ───────────────────────────────────────────────

    def add_handoff(self, h: HandoffPackage) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO handoffs (
                    id, lead_id, reason, urgency, summary, suggested_response,
                    conversation_excerpt, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    f"{h.lead_id}-{int(h.created_at.timestamp())}", h.lead_id, h.reason,
                    h.urgency, h.summary, h.suggested_response, h.conversation_excerpt,
                    _iso(h.created_at),
                ),
            )

    def add_notification(self, n: Notification) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO notifications (
                    id, kind, lead_id, title, message, urgency, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (n.id, n.kind, n.lead_id, n.title, n.message, n.urgency, _iso(n.created_at)),
            )

    def list_notifications(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Jobs ───────────────────────────────────────────────────────────────────

    def upsert_job(self, job: ReplyJob) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO reply_jobs (
                    id, status, total, handled, escalated, replies_sent,
                    meetings_booked, skipped, created_at, completed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.id, job.status, job.total, job.handled, job.escalated,
                    job.replies_sent, job.meetings_booked, job.skipped,
                    _iso(job.created_at), _iso(job.completed_at),
                ),
            )

    def close(self) -> None:
        self._conn.close()
