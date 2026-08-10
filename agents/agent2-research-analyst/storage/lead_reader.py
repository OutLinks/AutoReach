"""
Reads Lead records written by Agent 1 (JSONL files under
agents/agent1-lead-finder/output/).

Returns only A and B grade leads by default — those are the ones worth
researching deeply.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Generator, Optional
from urllib.request import Request, urlopen

from core.runtime_paths import agent_output_dir

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = agent_output_dir("agent1-lead-finder")


class LeadReader:
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        backend: Optional[str] = None,
        bridge_url: Optional[str] = None,
    ) -> None:
        self._dir = output_dir or _DEFAULT_OUTPUT_DIR
        self._backend = backend or os.getenv("AUTOREACH_LEAD_PIPELINE_BACKEND", "local").lower()
        self._bridge_url = (bridge_url or os.getenv(
            "AUTOREACH_D1_BRIDGE_URL", "http://autoreach.storage"
        )).rstrip("/")
        if self._backend not in {"local", "d1"}:
            raise ValueError("AUTOREACH_LEAD_PIPELINE_BACKEND must be 'local' or 'd1'")

    # ── Public API ─────────────────────────────────────────────────────────────

    def read_all(
        self,
        min_grade: str = "B",
        include_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        """Read all JSONL files and return qualifying leads."""
        if self._backend == "d1":
            return self._filter(self._d1_leads(), min_grade, include_duplicates)
        leads: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("leads_*.jsonl")):
            leads.extend(self._read_file(path, min_grade, include_duplicates))
        logger.info("LeadReader: read %d qualifying leads from %s", len(leads), self._dir)
        return leads

    def read_file(
        self,
        path: Path,
        min_grade: str = "B",
        include_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        return list(self._read_file(path, min_grade, include_duplicates))

    def read_by_id(self, lead_id: str) -> Optional[dict[str, Any]]:
        """Find a single lead by its ID across all JSONL files."""
        if self._backend == "d1":
            leads = self._d1_leads([lead_id])
            return leads[0] if leads else None
        for path in sorted(self._dir.glob("leads_*.jsonl")):
            for lead in self._stream(path):
                if lead.get("id") == lead_id:
                    return lead
        return None

    # ── Internal ───────────────────────────────────────────────────────────────

    def _d1_leads(self, lead_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
        request = Request(
            f"{self._bridge_url}/v1/lead-pipeline/list-leads",
            data=json.dumps({"stage": "scored", "lead_ids": lead_ids or []}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=15) as response:  # nosec B310 Worker virtual host
            value = json.loads(response.read().decode())
        if not isinstance(value, dict) or not isinstance(value.get("leads"), list):
            raise RuntimeError("D1 lead pipeline bridge returned an invalid response")
        return [lead for lead in value["leads"] if isinstance(lead, dict)]

    @staticmethod
    def _filter(
        leads: list[dict[str, Any]], min_grade: str, include_duplicates: bool
    ) -> list[dict[str, Any]]:
        grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "": 4}
        min_rank = grade_order.get(min_grade, 4)
        return [
            lead for lead in leads
            if (include_duplicates or not lead.get("is_duplicate"))
            and grade_order.get(lead.get("lead_grade") or "", 4) <= min_rank
        ]

    def _read_file(
        self,
        path: Path,
        min_grade: str,
        include_duplicates: bool,
    ) -> Generator[dict[str, Any], None, None]:
        yield from self._filter(list(self._stream(path)), min_grade, include_duplicates)

    @staticmethod
    def _stream(path: Path) -> Generator[dict[str, Any], None, None]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("LeadReader: bad JSON line in %s", path)
        except OSError as exc:
            logger.error("LeadReader: cannot open %s — %s", path, exc)
