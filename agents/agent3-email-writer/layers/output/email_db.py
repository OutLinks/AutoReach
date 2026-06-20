"""
Email database — SQLite-backed relational store for WrittenEmail records.

Schema:
  emails        — one row per written email, FK to lead_id + research_profile_id
  email_jobs    — one row per EmailJob run

The lead_id column is the FK chain back to Agent 1 (Lead.id) and
research_profile_id links to Agent 2 (ResearchProfile.id), giving a full
traceability chain: job → email → research → lead.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ...models import EmailJob, QualityReport, WrittenEmail

logger = logging.getLogger(__name__)

_CREATE_EMAILS = """
CREATE TABLE IF NOT EXISTS emails (
    id                   TEXT PRIMARY KEY,
    lead_id              TEXT NOT NULL,
    research_profile_id  TEXT NOT NULL,
    subject              TEXT NOT NULL,
    body                 TEXT NOT NULL,
    hook                 TEXT,
    cta                  TEXT,
    lead_first_name      TEXT,
    lead_last_name       TEXT,
    lead_company         TEXT,
    sender_name          TEXT,
    sender_email         TEXT,
    tone                 TEXT,
    template_name        TEXT,
    quality_score        REAL,
    quality_passed       INTEGER,
    quality_report       TEXT,
    status               TEXT DEFAULT 'draft',
    job_id               TEXT,
    created_at           TEXT
);
"""

_CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS email_jobs (
    id            TEXT PRIMARY KEY,
    status        TEXT,
    total         INTEGER,
    written       INTEGER,
    quality_passed INTEGER,
    quality_failed INTEGER,
    skipped       INTEGER,
    created_at    TEXT,
    completed_at  TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_emails_lead_id ON emails (lead_id);",
    "CREATE INDEX IF NOT EXISTS idx_emails_job_id ON emails (job_id);",
    "CREATE INDEX IF NOT EXISTS idx_emails_status ON emails (status);",
]


class EmailDatabase:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()
        logger.info("EmailDatabase: opened at %s", db_path)

    # ── Schema ─────────────────────────────────────────────────────────────────

    def _initialize(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_EMAILS)
            self._conn.execute(_CREATE_JOBS)
            for idx in _CREATE_INDEXES:
                self._conn.execute(idx)

    # ── Email CRUD ─────────────────────────────────────────────────────────────

    def insert_email(self, email: WrittenEmail) -> None:
        quality_report_json = (
            email.quality_report.model_dump_json()
            if email.quality_report
            else None
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO emails (
                    id, lead_id, research_profile_id, subject, body, hook, cta,
                    lead_first_name, lead_last_name, lead_company,
                    sender_name, sender_email, tone, template_name,
                    quality_score, quality_passed, quality_report,
                    status, job_id, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    email.id,
                    email.lead_id,
                    email.research_profile_id,
                    email.subject,
                    email.body,
                    email.hook,
                    email.cta,
                    email.lead_first_name,
                    email.lead_last_name,
                    email.lead_company,
                    email.sender_name,
                    email.sender_email,
                    email.tone,
                    email.template_name,
                    email.quality_score,
                    int(email.quality_passed),
                    quality_report_json,
                    email.status,
                    email.job_id,
                    email.created_at.isoformat(),
                ),
            )

    def get_email(self, email_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM emails WHERE id = ?", (email_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_emails_by_job(self, job_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM emails WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_emails_by_lead(self, lead_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM emails WHERE lead_id = ? ORDER BY created_at DESC",
            (lead_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, email_id: str, status: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE emails SET status = ? WHERE id = ?", (status, email_id)
            )

    def count_by_status(self, job_id: Optional[str] = None) -> dict[str, int]:
        if job_id:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) as n FROM emails WHERE job_id = ? GROUP BY status",
                (job_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) as n FROM emails GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ── Job CRUD ───────────────────────────────────────────────────────────────

    def upsert_job(self, job: EmailJob) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO email_jobs (
                    id, status, total, written, quality_passed, quality_failed,
                    skipped, created_at, completed_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.id,
                    job.status,
                    job.total,
                    job.written,
                    job.quality_passed,
                    job.quality_failed,
                    job.skipped,
                    job.created_at.isoformat(),
                    job.completed_at.isoformat() if job.completed_at else None,
                ),
            )

    def close(self) -> None:
        self._conn.close()
