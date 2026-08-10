"""Durable, single-consumer job queue backed by SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
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


class JobRepository(Protocol):
    """Persistence boundary for durable API jobs."""

    def create(
        self, kind: str, payload: dict[str, Any] | None = None, dedupe_key: str | None = None
    ) -> tuple[JobRecord, bool]: ...

    def get(self, job_id: str) -> JobRecord: ...

    def list(self, limit: int = 50, offset: int = 0) -> list[JobRecord]: ...

    def claim_for_execution(self, job_id: str) -> JobRecord | None: ...

    def mark_succeeded(self, job_id: str, result: Any) -> None: ...

    def mark_failed(self, job_id: str, error: str) -> None: ...

    def requeue_failed(self, job_id: str) -> JobRecord | None: ...

    def recover_incomplete(self) -> list[str]: ...

    def close(self) -> None: ...


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

    def claim_for_execution(self, job_id: str) -> JobRecord | None:
        """Atomically transition a queued job to running.

        The current SQLite adapter only supports local development, but this
        compare-and-set shape is deliberately the same primitive the D1 adapter
        will use with a lease token. It makes the direct execution path safe to
        call concurrently without two local workers executing the same job.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE api_jobs
                SET status = 'running', started_at = ?, error = ''
                WHERE id = ? AND status = 'queued'
                """,
                (_now(), job_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(job_id)

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

    def requeue_failed(self, job_id: str) -> JobRecord | None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE api_jobs
                SET status = 'queued', started_at = NULL, completed_at = NULL
                WHERE id = ? AND status = 'failed'
                """,
                (job_id,),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(job_id)

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


class D1JobStore:
    """Container-only JobRepository backed by the Worker D1 outbound bridge."""

    def __init__(self, bridge_url: str | None = None) -> None:
        self._bridge_url = (bridge_url or os.getenv("AUTOREACH_D1_BRIDGE_URL", "http://autoreach.storage")).rstrip("/")
        # Only the execution attempt that acquired this token may complete the
        # job. It protects a retried job from a late container response.
        self._leases: dict[str, str] = {}

    def create(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> tuple[JobRecord, bool]:
        response = self._call(
            "create",
            {
                "id": str(uuid4()),
                "kind": kind,
                "payload": payload or {},
                "dedupe_key": dedupe_key,
                "created_at": _now(),
            },
        )
        return self._job(response["job"]), bool(response["created"])

    def get(self, job_id: str) -> JobRecord:
        return self._job(self._call("get", {"id": job_id})["job"])

    def list(self, limit: int = 50, offset: int = 0) -> list[JobRecord]:
        response = self._call("list", {"limit": limit, "offset": offset})
        return [self._job(item) for item in response["jobs"]]

    def mark_running(self, job_id: str) -> None:
        self.claim_for_execution(job_id)

    def claim_for_execution(self, job_id: str) -> JobRecord | None:
        lease_token = str(uuid4())
        response = self._call(
            "claim",
            {
                "id": job_id,
                "started_at": _now(),
                "lease_token": lease_token,
                "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                "executor_id": os.getenv("HOSTNAME", "autoreach-container"),
            },
        )
        if not response["claimed"]:
            return None
        self._leases[job_id] = lease_token
        return self._job(response["job"])

    def mark_succeeded(self, job_id: str, result: Any) -> None:
        self._call(
            "succeed",
            {
                "id": job_id,
                "result": result,
                "completed_at": _now(),
                "lease_token": self._lease_for(job_id),
            },
        )
        self._leases.pop(job_id, None)

    def mark_failed(self, job_id: str, error: str) -> None:
        self._call(
            "fail",
            {
                "id": job_id,
                "error": error[:4000],
                "completed_at": _now(),
                "lease_token": self._lease_for(job_id),
            },
        )
        self._leases.pop(job_id, None)

    def requeue_failed(self, job_id: str) -> JobRecord | None:
        response = self._call("retry", {"id": job_id})
        if response.get("requeued"):
            self._leases.pop(job_id, None)
            return self._job(response["job"])
        return None

    def recover_incomplete(self) -> list[str]:
        return list(self._call("recover", {}).get("job_ids", []))

    def close(self) -> None:
        self._leases.clear()

    def _lease_for(self, job_id: str) -> str:
        try:
            return self._leases[job_id]
        except KeyError as exc:
            raise RuntimeError(f"No active D1 lease for job {job_id}") from exc

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self._bridge_url}/v1/jobs/{action}",
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:  # nosec B310 - Worker virtual hostname
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404:
                raise KeyError(payload.get("id", "")) from exc
            raise RuntimeError(f"D1 job bridge returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError("D1 job bridge is unavailable") from exc
        if not isinstance(body, dict):
            raise RuntimeError("D1 job bridge returned an invalid response")
        return body

    @staticmethod
    def _job(value: Any) -> JobRecord:
        if not isinstance(value, dict):
            raise RuntimeError("D1 job bridge did not return a job record")
        return JobRecord.model_validate(value)


def build_job_repository(path: Path, backend: str = "sqlite") -> JobRepository:
    """Select local SQLite or the container-only D1 job repository."""
    if backend == "d1":
        return D1JobStore()
    return JobStore(path)
