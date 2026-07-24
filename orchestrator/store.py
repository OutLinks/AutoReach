"""
Orchestrator store — the master SQLite record of the pipeline.

Tables:
  leads        — one row per PipelineLead (its lifecycle state + priority inputs)
  runs         — history of every stage invocation (for monitoring + reporting)
  events       — append-only audit log of state transitions
  dead_letter  — leads that exhausted retries, awaiting human review

The agents own their own databases (leads.jsonl, emails.db, sends.db,
conversations.db); this store owns *coordination state* only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .campaigns import CampaignBrief
from .models import PipelineLead, RunRecord

logger = logging.getLogger(__name__)

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS leads (
        id                 TEXT PRIMARY KEY,
        state              TEXT,
        email              TEXT,
        company            TEXT,
        industry           TEXT,
        quality_score      REAL,
        company_size_score REAL,
        industry_fit_score REAL,
        recency_score      REAL,
        manual_bump        INTEGER DEFAULT 0,
        priority           REAL,
        attempts           INTEGER DEFAULT 0,
        last_error         TEXT,
        retry_after        TEXT,
        discovered_at      TEXT,
        sent_at            TEXT,
        replied_at         TEXT,
        state_entered_at   TEXT,
        source_job         TEXT,
        metadata           TEXT,
        created_at         TEXT,
        updated_at         TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id               TEXT PRIMARY KEY,
        stage            TEXT,
        agent            TEXT,
        processed        INTEGER,
        succeeded        INTEGER,
        failed           INTEGER,
        ok               INTEGER,
        error            TEXT,
        started_at       TEXT,
        duration_seconds REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id    TEXT,
        from_state TEXT,
        to_state   TEXT,
        note       TEXT,
        at         TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS dead_letter (
        lead_id   TEXT PRIMARY KEY,
        stage     TEXT,
        reason    TEXT,
        added_at  TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        id          TEXT PRIMARY KEY,
        user_prompt TEXT NOT NULL,
        brief       TEXT NOT NULL,
        status      TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    """,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_leads_state ON leads (state);",
    "CREATE INDEX IF NOT EXISTS idx_runs_stage ON runs (stage);",
    "CREATE INDEX IF NOT EXISTS idx_events_lead ON events (lead_id);",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns (status);",
]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class OrchestratorStore:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            for idx in _INDEXES:
                self._conn.execute(idx)
        logger.info("OrchestratorStore: opened at %s", db_path)

    # ── Leads ──────────────────────────────────────────────────────────────────

    def upsert_lead(self, lead: PipelineLead) -> None:
        lead.updated_at = datetime.utcnow()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO leads (
                    id, state, email, company, industry, quality_score,
                    company_size_score, industry_fit_score, recency_score,
                    manual_bump, priority, attempts, last_error, retry_after,
                    discovered_at, sent_at, replied_at, state_entered_at,
                    source_job, metadata, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    lead.id, lead.state, lead.email, lead.company, lead.industry,
                    lead.quality_score, lead.company_size_score, lead.industry_fit_score,
                    lead.recency_score, int(lead.manual_bump), lead.priority,
                    lead.attempts, lead.last_error, _iso(lead.retry_after),
                    _iso(lead.discovered_at), _iso(lead.sent_at), _iso(lead.replied_at),
                    _iso(lead.state_entered_at), lead.source_job,
                    json.dumps(lead.metadata), _iso(lead.created_at), _iso(lead.updated_at),
                ),
            )

    def get_lead(self, lead_id: str) -> Optional[PipelineLead]:
        row = self._conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return self._row_to_lead(row) if row else None

    def leads_in_state(self, state: str) -> list[PipelineLead]:
        rows = self._conn.execute(
            "SELECT * FROM leads WHERE state = ?", (state,)
        ).fetchall()
        return [self._row_to_lead(r) for r in rows]

    def count_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) n FROM leads GROUP BY state"
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    def all_leads(self) -> list[PipelineLead]:
        rows = self._conn.execute("SELECT * FROM leads").fetchall()
        return [self._row_to_lead(r) for r in rows]

    @staticmethod
    def _row_to_lead(row: sqlite3.Row) -> PipelineLead:
        return PipelineLead(
            id=row["id"], state=row["state"], email=row["email"] or "",
            company=row["company"] or "", industry=row["industry"] or "",
            quality_score=row["quality_score"] or 0.0,
            company_size_score=row["company_size_score"] or 0.0,
            industry_fit_score=row["industry_fit_score"] or 0.0,
            recency_score=row["recency_score"] if row["recency_score"] is not None else 1.0,
            manual_bump=bool(row["manual_bump"]), priority=row["priority"] or 0.0,
            attempts=row["attempts"] or 0, last_error=row["last_error"] or "",
            retry_after=_dt(row["retry_after"]),
            discovered_at=_dt(row["discovered_at"]), sent_at=_dt(row["sent_at"]),
            replied_at=_dt(row["replied_at"]),
            state_entered_at=_dt(row["state_entered_at"]) or datetime.utcnow(),
            source_job=row["source_job"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_dt(row["updated_at"]) or datetime.utcnow(),
        )

    # ── Events (audit) ─────────────────────────────────────────────────────────

    def log_event(self, lead_id: str, from_state: str, to_state: str, note: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO events (lead_id, from_state, to_state, note, at) VALUES (?,?,?,?,?)",
                (lead_id, from_state, to_state, note, _iso(datetime.utcnow())),
            )

    def count_transitions_to(self, state: str) -> int:
        """How many leads have ever entered a state (cumulative, from the audit log)."""
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT lead_id) n FROM events WHERE to_state = ?", (state,)
        ).fetchone()
        return row["n"] or 0

    # ── Runs (history) ─────────────────────────────────────────────────────────

    def record_run(self, run: RunRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    id, stage, agent, processed, succeeded, failed, ok, error,
                    started_at, duration_seconds
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run.id, run.stage, run.agent, run.processed, run.succeeded,
                    run.failed, int(run.ok), run.error, _iso(run.started_at),
                    run.duration_seconds,
                ),
            )

    def recent_runs(self, stage: str, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE stage = ? ORDER BY started_at DESC LIMIT ?",
            (stage, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Dead letter ────────────────────────────────────────────────────────────

    def dead_letter(self, lead_id: str, stage: str, reason: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO dead_letter (lead_id, stage, reason, added_at) VALUES (?,?,?,?)",
                (lead_id, stage, reason, _iso(datetime.utcnow())),
            )

    def dead_letter_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) n FROM dead_letter").fetchone()
        return row["n"] or 0

    def list_dead_letter(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM dead_letter ORDER BY added_at").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    # ── Campaign briefs ───────────────────────────────────────────────────────

    def save_campaign(self, brief: CampaignBrief) -> None:
        """Persist a draft/active campaign and keep at most one active brief."""
        brief.updated_at = datetime.utcnow()
        with self._conn:
            if brief.status == "active":
                self._conn.execute("UPDATE campaigns SET status = 'draft' WHERE status = 'active'")
            self._conn.execute(
                """
                INSERT OR REPLACE INTO campaigns (id, user_prompt, brief, status, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    brief.id,
                    brief.user_prompt,
                    brief.model_dump_json(),
                    brief.status,
                    _iso(brief.created_at),
                    _iso(brief.updated_at),
                ),
            )

    def get_campaign(self, campaign_id: str) -> Optional[CampaignBrief]:
        row = self._conn.execute("SELECT brief, status FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not row:
            return None
        brief = CampaignBrief.model_validate_json(row["brief"])
        brief.status = row["status"]
        return brief

    def active_campaign(self) -> Optional[CampaignBrief]:
        row = self._conn.execute(
            "SELECT brief, status FROM campaigns WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        brief = CampaignBrief.model_validate_json(row["brief"])
        brief.status = row["status"]
        return brief

    def list_campaigns(self, limit: int = 100, offset: int = 0) -> list[CampaignBrief]:
        rows = self._conn.execute(
            "SELECT brief, status FROM campaigns ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        campaigns: list[CampaignBrief] = []
        for row in rows:
            brief = CampaignBrief.model_validate_json(row["brief"])
            brief.status = row["status"]
            campaigns.append(brief)
        return campaigns

    def activate_campaign(self, campaign_id: str) -> CampaignBrief:
        brief = self.get_campaign(campaign_id)
        if not brief:
            raise ValueError(f"Campaign {campaign_id} does not exist")
        brief.status = "active"
        self.save_campaign(brief)
        return brief
