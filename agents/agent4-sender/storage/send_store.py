"""
Send store — SQLite-backed persistence for everything Agent 4 produces.

Tables:
  sent_emails       — one row per delivery attempt (FK → Agent 3 emails.id)
  tracking_events   — delivery/engagement events (FK → sent_emails.id)
  sequence_states   — per-lead follow-up sequence position
  sending_accounts  — sending identities + their volume counters & health
  suppression_list  — addresses/domains that must never be emailed
  send_jobs         — one row per batch run

This is the single source of truth for the tracking and reputation layers, which
read aggregate counts straight out of these tables.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import (
    ReputationStatus,
    SendingAccount,
    SendJob,
    SentEmail,
    SequenceState,
    SuppressionEntry,
    TrackingEvent,
)

logger = logging.getLogger(__name__)

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS sent_emails (
        id            TEXT PRIMARY KEY,
        email_id      TEXT NOT NULL,
        lead_id       TEXT NOT NULL,
        step          TEXT,
        recipient     TEXT,
        account_email TEXT,
        provider      TEXT,
        message_id    TEXT,
        subject       TEXT,
        body          TEXT,
        status        TEXT DEFAULT 'queued',
        opened        INTEGER DEFAULT 0,
        clicked       INTEGER DEFAULT 0,
        replied       INTEGER DEFAULT 0,
        bounced       INTEGER DEFAULT 0,
        sent_at       TEXT,
        job_id        TEXT,
        created_at    TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tracking_events (
        id             TEXT PRIMARY KEY,
        sent_email_id  TEXT NOT NULL,
        lead_id        TEXT,
        event_type     TEXT NOT NULL,
        detail         TEXT,
        bounce_type    TEXT,
        occurred_at    TEXT,
        metadata       TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sequence_states (
        lead_id        TEXT PRIMARY KEY,
        email_id       TEXT,
        current_step   TEXT,
        status         TEXT,
        steps_sent     TEXT,
        next_send_at   TEXT,
        initial_sent_at TEXT,
        recipient      TEXT,
        account_email  TEXT,
        timezone       TEXT,
        created_at     TEXT,
        updated_at     TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sending_accounts (
        email             TEXT PRIMARY KEY,
        provider          TEXT,
        display_name      TEXT,
        daily_limit       INTEGER,
        hourly_limit      INTEGER,
        sent_today        INTEGER DEFAULT 0,
        sent_this_hour    INTEGER DEFAULT 0,
        health_score      REAL DEFAULT 1.0,
        status            TEXT DEFAULT 'active',
        warmup_start_date TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS suppression_list (
        value      TEXT PRIMARY KEY,
        is_domain  INTEGER DEFAULT 0,
        reason     TEXT,
        detail     TEXT,
        added_at   TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS send_jobs (
        id           TEXT PRIMARY KEY,
        kind         TEXT,
        status       TEXT,
        total        INTEGER,
        sent         INTEGER,
        skipped      INTEGER,
        failed       INTEGER,
        suppressed   INTEGER,
        created_at   TEXT,
        completed_at TEXT
    );
    """,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sent_lead ON sent_emails (lead_id);",
    "CREATE INDEX IF NOT EXISTS idx_sent_job ON sent_emails (job_id);",
    "CREATE INDEX IF NOT EXISTS idx_sent_status ON sent_emails (status);",
    "CREATE INDEX IF NOT EXISTS idx_event_sent ON tracking_events (sent_email_id);",
    "CREATE INDEX IF NOT EXISTS idx_event_type ON tracking_events (event_type);",
]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class SendStore:
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
        logger.info("SendStore: opened at %s", db_path)

    # ── Sent emails ────────────────────────────────────────────────────────────

    def insert_sent(self, s: SentEmail) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO sent_emails (
                    id, email_id, lead_id, step, recipient, account_email,
                    provider, message_id, subject, body, status,
                    opened, clicked, replied, bounced, sent_at, job_id, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    s.id, s.email_id, s.lead_id, s.step, s.recipient, s.account_email,
                    s.provider, s.message_id, s.subject, s.body, s.status,
                    int(s.opened), int(s.clicked), int(s.replied), int(s.bounced),
                    _iso(s.sent_at), s.job_id, _iso(s.created_at),
                ),
            )

    def update_sent_status(self, sent_id: str, status: str, **flags: bool) -> None:
        cols = ["status = ?"]
        params: list = [status]
        for flag, value in flags.items():
            cols.append(f"{flag} = ?")
            params.append(int(value))
        params.append(sent_id)
        with self._conn:
            self._conn.execute(
                f"UPDATE sent_emails SET {', '.join(cols)} WHERE id = ?", params
            )

    def get_sent(self, sent_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM sent_emails WHERE id = ?", (sent_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_sent_by_message_id(self, message_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM sent_emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        return dict(row) if row else None

    def latest_sent_for_lead(self, lead_id: str) -> Optional[dict]:
        """Most recent send to a lead — used to thread follow-ups."""
        row = self._conn.execute(
            "SELECT * FROM sent_emails WHERE lead_id = ? ORDER BY created_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_sent(self, status: Optional[str] = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM sent_emails WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM sent_emails ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Tracking events ────────────────────────────────────────────────────────

    def insert_event(self, e: TrackingEvent) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO tracking_events (
                    id, sent_email_id, lead_id, event_type, detail,
                    bounce_type, occurred_at, metadata
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    e.id, e.sent_email_id, e.lead_id, e.event_type, e.detail,
                    e.bounce_type, _iso(e.occurred_at), json.dumps(e.metadata),
                ),
            )

    def get_events(self, sent_email_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tracking_events WHERE sent_email_id = ? ORDER BY occurred_at",
            (sent_email_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_events_by_type(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT event_type, COUNT(*) n FROM tracking_events GROUP BY event_type"
        ).fetchall()
        return {r["event_type"]: r["n"] for r in rows}

    # ── Sequence states ────────────────────────────────────────────────────────

    def upsert_sequence(self, st: SequenceState) -> None:
        st.updated_at = datetime.utcnow()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO sequence_states (
                    lead_id, email_id, current_step, status, steps_sent,
                    next_send_at, initial_sent_at, recipient, account_email,
                    timezone, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    st.lead_id, st.email_id, st.current_step, st.status,
                    json.dumps(st.steps_sent), _iso(st.next_send_at),
                    _iso(st.initial_sent_at), st.recipient, st.account_email,
                    st.timezone, _iso(st.created_at), _iso(st.updated_at),
                ),
            )

    def get_sequence(self, lead_id: str) -> Optional[SequenceState]:
        row = self._conn.execute(
            "SELECT * FROM sequence_states WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        return self._row_to_sequence(row) if row else None

    def list_active_sequences(self) -> list[SequenceState]:
        rows = self._conn.execute(
            "SELECT * FROM sequence_states WHERE status = 'active'"
        ).fetchall()
        return [self._row_to_sequence(r) for r in rows]

    @staticmethod
    def _row_to_sequence(row: sqlite3.Row) -> SequenceState:
        return SequenceState(
            lead_id=row["lead_id"],
            email_id=row["email_id"] or "",
            current_step=row["current_step"],
            status=row["status"],
            steps_sent=json.loads(row["steps_sent"] or "[]"),
            next_send_at=_dt(row["next_send_at"]),
            initial_sent_at=_dt(row["initial_sent_at"]),
            recipient=row["recipient"] or "",
            account_email=row["account_email"] or "",
            timezone=row["timezone"] or "UTC",
            created_at=_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_dt(row["updated_at"]) or datetime.utcnow(),
        )

    # ── Sending accounts ───────────────────────────────────────────────────────

    def upsert_account(self, a: SendingAccount) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO sending_accounts (
                    email, provider, display_name, daily_limit, hourly_limit,
                    sent_today, sent_this_hour, health_score, status, warmup_start_date
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    a.email, a.provider, a.display_name, a.daily_limit, a.hourly_limit,
                    a.sent_today, a.sent_this_hour, a.health_score, a.status,
                    _iso(a.warmup_start_date),
                ),
            )

    def list_accounts(self) -> list[SendingAccount]:
        rows = self._conn.execute("SELECT * FROM sending_accounts").fetchall()
        return [
            SendingAccount(
                email=r["email"],
                provider=r["provider"] or "smtp",
                display_name=r["display_name"] or "",
                daily_limit=r["daily_limit"] or 50,
                hourly_limit=r["hourly_limit"] or 10,
                sent_today=r["sent_today"] or 0,
                sent_this_hour=r["sent_this_hour"] or 0,
                health_score=r["health_score"] if r["health_score"] is not None else 1.0,
                status=r["status"] or "active",
                warmup_start_date=_dt(r["warmup_start_date"]),
            )
            for r in rows
        ]

    # ── Suppression list ───────────────────────────────────────────────────────

    def add_suppression(self, entry: SuppressionEntry) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO suppression_list (
                    value, is_domain, reason, detail, added_at
                ) VALUES (?,?,?,?,?)
                """,
                (
                    entry.value.lower(), int(entry.is_domain), entry.reason,
                    entry.detail, _iso(entry.added_at),
                ),
            )

    def is_suppressed(self, email: str) -> bool:
        email = email.lower()
        domain = email.split("@")[-1] if "@" in email else ""
        row = self._conn.execute(
            """
            SELECT 1 FROM suppression_list
            WHERE (is_domain = 0 AND value = ?)
               OR (is_domain = 1 AND value = ?)
            LIMIT 1
            """,
            (email, domain),
        ).fetchone()
        return row is not None

    def list_suppressions(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM suppression_list").fetchall()
        return [dict(r) for r in rows]

    # ── Jobs ───────────────────────────────────────────────────────────────────

    def upsert_job(self, job: SendJob) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO send_jobs (
                    id, kind, status, total, sent, skipped, failed,
                    suppressed, created_at, completed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.id, job.kind, job.status, job.total, job.sent, job.skipped,
                    job.failed, job.suppressed, _iso(job.created_at),
                    _iso(job.completed_at),
                ),
            )

    # ── Aggregate metrics (used by reputation layer) ───────────────────────────

    def metrics(self, account_email: Optional[str] = None) -> dict[str, int]:
        """Return raw counts used to compute reputation."""
        where = ""
        params: tuple = ()
        if account_email and account_email != "*":
            where = "WHERE account_email = ?"
            params = (account_email,)

        row = self._conn.execute(
            f"""
            SELECT
                COUNT(*) AS sent,
                SUM(CASE WHEN status IN ('delivered','opened','clicked','replied') THEN 1 ELSE 0 END) AS delivered,
                SUM(bounced) AS bounced,
                SUM(CASE WHEN status = 'complained' THEN 1 ELSE 0 END) AS complained,
                SUM(opened) AS opened
            FROM sent_emails {where}
            """,
            params,
        ).fetchone()
        return {
            "sent": row["sent"] or 0,
            "delivered": row["delivered"] or 0,
            "bounced": row["bounced"] or 0,
            "complained": row["complained"] or 0,
            "opened": row["opened"] or 0,
        }

    def close(self) -> None:
        self._conn.close()
