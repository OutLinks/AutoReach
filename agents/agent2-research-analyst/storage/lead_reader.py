"""
Reads Lead records written by Agent 1 (JSONL files under
agents/agent1-lead-finder/output/).

Returns only A and B grade leads by default — those are the ones worth
researching deeply.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Generator, Optional

from core.runtime_paths import agent_output_dir

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = agent_output_dir("agent1-lead-finder")


class LeadReader:
    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self._dir = output_dir or _DEFAULT_OUTPUT_DIR

    # ── Public API ─────────────────────────────────────────────────────────────

    def read_all(
        self,
        min_grade: str = "B",
        include_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        """Read all JSONL files and return qualifying leads."""
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
        for path in sorted(self._dir.glob("leads_*.jsonl")):
            for lead in self._stream(path):
                if lead.get("id") == lead_id:
                    return lead
        return None

    # ── Internal ───────────────────────────────────────────────────────────────

    def _read_file(
        self,
        path: Path,
        min_grade: str,
        include_duplicates: bool,
    ) -> Generator[dict[str, Any], None, None]:
        grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "": 4}
        min_rank = grade_order.get(min_grade, 4)

        for lead in self._stream(path):
            if not include_duplicates and lead.get("is_duplicate"):
                continue
            grade = lead.get("lead_grade") or ""
            if grade_order.get(grade, 4) > min_rank:
                continue
            yield lead

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
