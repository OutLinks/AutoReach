"""
Reads approved WrittenEmail records from Agent 3's SQLite database
(agents/agent3-email-writer/output/emails.db).

Agent 4 only sends emails Agent 3 marked "approved" (passed quality). Agent 3's
rows carry the content and lead identity but NOT the recipient's address or
location — those live in Agent 1's lead output. So each email is enriched here by
joining back to the Agent 1 leads JSONL on lead_id, attaching `recipient`,
`timezone`, and location fields the scheduling layer needs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

_AGENT1_OUTPUT_DIR = (
    Path(__file__).parent.parent.parent / "agent1-lead-finder" / "output"
)


class EmailReader:
    def __init__(
        self,
        emails_db_path: str,
        leads_dir: Optional[Path] = None,
    ) -> None:
        self._path = emails_db_path
        self._leads_dir = leads_dir or _AGENT1_OUTPUT_DIR
        self._lead_index: dict[str, dict] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def read_sendable(
        self,
        statuses: Optional[list[str]] = None,
        lead_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Return email rows ready to send.

        Args:
            statuses: WrittenEmail.status values to include (default: ["approved"]).
            lead_ids: restrict to these lead IDs (all if None).
        """
        statuses = statuses or ["approved"]
        if not Path(self._path).exists():
            logger.warning("EmailReader: Agent 3 DB not found at %s", self._path)
            return []

        placeholders = ",".join("?" for _ in statuses)
        sql = f"SELECT * FROM emails WHERE status IN ({placeholders})"
        params: list = list(statuses)

        if lead_ids:
            sql += " AND lead_id IN (%s)" % ",".join("?" for _ in lead_ids)
            params.extend(lead_ids)

        sql += " ORDER BY created_at"

        try:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            logger.error("EmailReader: query failed — %s", exc)
            return []

        emails = [self._enrich(dict(r)) for r in rows]
        logger.info("EmailReader: loaded %d sendable emails", len(emails))
        return emails

    def read_by_id(self, email_id: str) -> Optional[dict]:
        if not Path(self._path).exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM emails WHERE id = ?", (email_id,)
            ).fetchone()
            conn.close()
        except sqlite3.Error as exc:
            logger.error("EmailReader: lookup failed — %s", exc)
            return None
        return self._enrich(dict(row)) if row else None

    # ── Lead enrichment (join back to Agent 1) ─────────────────────────────────

    def _enrich(self, email: dict) -> dict:
        """Attach recipient address + location from the originating lead."""
        lead = self._lead_for(email.get("lead_id", ""))
        if lead:
            email.setdefault("recipient", lead.get("email") or "")
            email["timezone"] = lead.get("timezone") or ""
            email["city"] = lead.get("city") or ""
            email["state"] = lead.get("state") or ""
            email["country"] = lead.get("country") or ""
            email["location"] = " ".join(
                str(p) for p in (lead.get("city"), lead.get("state"), lead.get("country")) if p
            )
        else:
            email.setdefault("recipient", "")
        return email

    def _lead_for(self, lead_id: str) -> Optional[dict]:
        if not lead_id:
            return None
        if not self._lead_index:
            self._build_lead_index()
        return self._lead_index.get(lead_id)

    def _build_lead_index(self) -> None:
        if not self._leads_dir.exists():
            logger.warning("EmailReader: Agent 1 leads dir not found at %s", self._leads_dir)
            return
        for path in sorted(self._leads_dir.glob("leads_*.jsonl")):
            for lead in self._stream(path):
                lid = lead.get("id")
                if lid:
                    self._lead_index[lid] = lead
        logger.debug("EmailReader: indexed %d leads", len(self._lead_index))

    @staticmethod
    def _stream(path: Path) -> Generator[dict, None, None]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("EmailReader: bad JSON line in %s", path)
        except OSError as exc:
            logger.error("EmailReader: cannot open %s — %s", path, exc)
