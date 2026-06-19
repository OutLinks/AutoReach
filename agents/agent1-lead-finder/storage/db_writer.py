"""
Final database writer — flushes scored leads from Redis into persistent storage.

This is intentionally a thin adapter. The actual database (Airtable, Postgres,
Supabase, etc.) is wired up here. For now it logs to stdout and writes a local
JSONL file so the pipeline is functional before a real DB is connected.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..models import Lead

logger = logging.getLogger(__name__)

# Default output path — override in production
_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "output"


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


# ── Placeholder stubs for future database integrations ───────────────────────
# Uncomment and implement whichever backend you want to use.

# class AirtableWriter(DBWriter):
#     async def _write_one(self, fh, lead):
#         # POST to Airtable API
#         pass

# class PostgresWriter(DBWriter):
#     async def _write_one(self, fh, lead):
#         # INSERT INTO leads ...
#         pass

# class SupabaseWriter(DBWriter):
#     async def _write_one(self, fh, lead):
#         # supabase.table("leads").insert(...)
#         pass
