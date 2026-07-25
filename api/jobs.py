"""Durable, single-consumer job queue backed by SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRecord(BaseModel):
    id: str
    kind: str
    status: JobStatus = "queued"
    payload: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str = ""
    dedupe_key: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    result TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_api_jobs_status_created
                    ON api_jobs (status, created_at);
                """
            )

    def create(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> tuple[JobRecord, bool]:
        job_id = str(uuid4())
        created_at = _now()
        body = json.dumps(payload or {}, default=str)
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO api_jobs
                            (id, kind, status, payload, dedupe_key, created_at)
                        VALUES (?, ?, 'queued', ?, ?, ?)
                        """,
                        (job_id, kind, body, dedupe_key, created_at),
                    )
            except sqlite3.IntegrityError:
                if not dedupe_key:
                    raise
                row = self._conn.execute(
                    "SELECT * FROM api_jobs WHERE dedupe_key = ?", (dedupe_key,)
                ).fetchone()
                if row is None:
                    raise
                return self._row(row), False
        return self.get(job_id), True

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row(row)

    def list(self, limit: int = 50, offset: int = 0) -> list[JobRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM api_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row(row) for row in rows]

    def mark_running(self, job_id: str) -> None:
        self._update(job_id, status="running", started_at=_now(), error="")

    def mark_succeeded(self, job_id: str, result: Any) -> None:
        self._update(
            job_id,
            status="succeeded",
            result=json.dumps(result, default=str),
            completed_at=_now(),
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self._update(
            job_id,
            status="failed",
            error=error[:4000],
            completed_at=_now(),
        )

    def recover_incomplete(self) -> list[str]:
        """Requeue work interrupted by a process restart."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE api_jobs
                SET status = 'queued', started_at = NULL,
                    error = CASE WHEN status = 'running' THEN 'Recovered after restart' ELSE error END
                WHERE status IN ('queued', 'running')
                """
            )
            rows = self._conn.execute(
                "SELECT id FROM api_jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [row["id"] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _update(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"UPDATE api_jobs SET {columns} WHERE id = ?",
                (*values.values(), job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            payload=json.loads(row["payload"] or "{}"),
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"] or "",
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )
