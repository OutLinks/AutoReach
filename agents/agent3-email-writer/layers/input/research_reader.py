"""
Reads ResearchProfile records from Agent 2's JSONL output.

Pairs each research profile with its originating lead data (from Agent 1)
so the email writer has the full picture in one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Generator, Optional

from core.runtime_paths import agent_output_dir

logger = logging.getLogger(__name__)

_AGENT2_OUTPUT_DIR = agent_output_dir("agent2-research-analyst")
_AGENT1_OUTPUT_DIR = agent_output_dir("agent1-lead-finder")


class ResearchReader:
    def __init__(
        self,
        research_dir: Optional[Path] = None,
        leads_dir: Optional[Path] = None,
    ) -> None:
        self._research_dir = research_dir or _AGENT2_OUTPUT_DIR
        self._leads_dir = leads_dir or _AGENT1_OUTPUT_DIR
        self._leads_index: dict[str, dict] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def read_all(
        self,
        min_quality_score: float = 5.0,
        statuses: Optional[list[str]] = None,
    ) -> list[tuple[dict, dict]]:
        """
        Returns list of (research_profile, lead) pairs, filtered by quality.

        Args:
            min_quality_score: Minimum AI quality score (1–10) from Agent 2.
            statuses: Include only profiles with these statuses (default: complete + partial).
        """
        if statuses is None:
            statuses = ["complete", "partial"]

        self._build_lead_index()

        pairs: list[tuple[dict, dict]] = []
        for path in sorted(self._research_dir.glob("research_*.jsonl")):
            for profile in self._stream(path):
                if profile.get("status") not in statuses:
                    continue
                score = (profile.get("quality_score") or {}).get("score", 0.0)
                if score < min_quality_score:
                    continue
                lead_id = profile.get("lead_id", "")
                lead = self._leads_index.get(lead_id)
                if not lead:
                    logger.warning(
                        "ResearchReader: no lead found for lead_id=%s", lead_id
                    )
                    continue
                pairs.append((profile, lead))

        logger.info("ResearchReader: loaded %d research/lead pairs", len(pairs))
        return pairs

    def read_by_lead_ids(self, lead_ids: list[str]) -> list[tuple[dict, dict]]:
        self._build_lead_index()
        id_set = set(lead_ids)
        pairs = []
        for path in sorted(self._research_dir.glob("research_*.jsonl")):
            for profile in self._stream(path):
                if profile.get("lead_id") in id_set:
                    lead = self._leads_index.get(profile["lead_id"])
                    if lead:
                        pairs.append((profile, lead))
        return pairs

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_lead_index(self) -> None:
        if self._leads_index:
            return
        for path in sorted(self._leads_dir.glob("leads_*.jsonl")):
            for lead in self._stream(path):
                lid = lead.get("id")
                if lid:
                    self._leads_index[lid] = lead
        logger.debug("ResearchReader: indexed %d leads", len(self._leads_index))

    @staticmethod
    def _stream(path: Path) -> Generator[dict[str, Any], None, None]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("ResearchReader: bad JSON line in %s", path)
        except OSError as exc:
            logger.error("ResearchReader: cannot open %s — %s", path, exc)
