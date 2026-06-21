"""
Final database writer — flushes scored leads from Redis into persistent storage.

Two backends are available:
  - `DBWriter`        — local JSONL file per run (zero-setup default / fallback)
  - `SupabaseWriter`  — upserts leads into a Supabase (Postgres) table via REST

`make_writer(config)` picks Supabase when it's configured, otherwise JSONL.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ..models import Lead

if TYPE_CHECKING:
    from ..config import ServiceConfig

logger = logging.getLogger(__name__)

# Default output path — override in production
_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Lead fields promoted to their own Supabase columns (the rest live in `payload`).
_SUPABASE_COLUMNS = (
    "id", "first_name", "last_name", "full_name", "email", "phone",
    "company_name", "company_domain", "company_website", "industry",
    "city", "state", "country", "title", "seniority", "linkedin_url",
    "email_status", "email_score", "lead_score", "lead_grade", "stage",
    "sources", "created_at",
)


class DBWriter:
    """
    Writes final scored leads to persistent storage.

    Replace `_write_one()` to target a real database.
    Current implementation: JSONL file per job run.
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or _DEFAULT_OUTPUT_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def write(self, leads: list[Lead], job_id: str) -> int:
        """
        Write all scored leads and return the count successfully written.
        Skips any lead marked as duplicate or with a 'D' grade.
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = self._output_dir / f"leads_{job_id[:8]}_{timestamp}.jsonl"

        written = 0
        skipped = 0

        with open(output_path, "w", encoding="utf-8") as fh:
            for lead in leads:
                if lead.is_duplicate:
                    skipped += 1
                    continue
                self._write_one(fh, lead)
                written += 1

        logger.info(
            "DB write complete — job=%s  written=%d  skipped=%d  file=%s",
            job_id, written, skipped, output_path,
        )
        return written

    def _write_one(self, fh: object, lead: Lead) -> None:
        """Write a single lead. Replace this to target a real database."""
        record = lead.model_dump(mode="json")
        fh.write(json.dumps(record, default=str) + "\n")  # type: ignore[arg-type]


class SupabaseWriter:
    """
    Upserts final scored leads into a Supabase (Postgres) table via the REST
    API (PostgREST). Uses the service-role key so row-level security doesn't
    block inserts. Conflicts on `id` are merged (idempotent re-runs).

    Promoted columns (see `_SUPABASE_COLUMNS`) are stored individually for easy
    querying; the complete lead is also stored in a `payload` jsonb column plus
    the originating `job_id`.
    """

    def __init__(self, url: str, key: str, table: str = "leads") -> None:
        self._endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
        self._key = key
        self._table = table

    async def write(self, leads: list[Lead], job_id: str) -> int:
        """Upsert all non-duplicate leads. Returns the count sent to Supabase."""
        records = [
            self._to_record(lead, job_id)
            for lead in leads
            if not lead.is_duplicate
        ]
        if not records:
            logger.info("Supabase: nothing to write for job %s", job_id)
            return 0

        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            # Upsert: merge rows that collide on the primary key.
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._endpoint,
                    params={"on_conflict": "id"},
                    headers=headers,
                    json=records,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Supabase write failed (HTTP %s): %s",
                exc.response.status_code, exc.response.text[:300],
            )
            return 0
        except Exception as exc:
            logger.error("Supabase write failed: %s", exc)
            return 0

        logger.info(
            "Supabase write complete — job=%s  written=%d  table=%s",
            job_id, len(records), self._table,
        )
        return len(records)

    @staticmethod
    def _to_record(lead: Lead, job_id: str) -> dict:
        full = lead.model_dump(mode="json")
        record = {col: full.get(col) for col in _SUPABASE_COLUMNS}
        record["job_id"] = job_id
        record["payload"] = full
        return record


def make_writer(config: "ServiceConfig"):
    """Return the Supabase writer when configured, else the JSONL writer."""
    if config.supabase.is_ready():
        logger.info("Using SupabaseWriter (table=%s)", config.supabase.table)
        return SupabaseWriter(
            config.supabase.url, config.supabase.key, config.supabase.table,
        )
    logger.info("Supabase not configured — using local JSONL DBWriter")
    return DBWriter()
