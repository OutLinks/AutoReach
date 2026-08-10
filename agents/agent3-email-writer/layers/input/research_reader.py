"""
Reads ResearchProfile records from Agent 2's JSONL output.

Pairs each research profile with its originating lead data (from Agent 1)
so the email writer has the full picture in one place.
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

_AGENT2_OUTPUT_DIR = agent_output_dir("agent2-research-analyst")
_AGENT1_OUTPUT_DIR = agent_output_dir("agent1-lead-finder")


class ResearchReader:
    def __init__(
        self,
        research_dir: Optional[Path] = None,
        leads_dir: Optional[Path] = None,
        backend: Optional[str] = None,
        bridge_url: Optional[str] = None,
    ) -> None:
        self._research_dir = research_dir or _AGENT2_OUTPUT_DIR
        self._leads_dir = leads_dir or _AGENT1_OUTPUT_DIR
        self._backend = backend or os.getenv("AUTOREACH_RESEARCH_READER_BACKEND", "local").lower()
        self._bridge_url = (bridge_url or os.getenv(
            "AUTOREACH_D1_BRIDGE_URL", "http://autoreach.storage"
        )).rstrip("/")
        if self._backend not in {"local", "d1"}:
            raise ValueError("AUTOREACH_RESEARCH_READER_BACKEND must be 'local' or 'd1'")
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

        if self._backend == "d1":
            return self._read_d1(min_quality_score, statuses)

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
        if self._backend == "d1":
            return self._read_d1(0.0, ["complete", "partial"], lead_ids)
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

    def _read_d1(
        self,
        min_quality_score: float,
        statuses: list[str],
        lead_ids: Optional[list[str]] = None,
    ) -> list[tuple[dict, dict]]:
        index = self._bridge_json("research-index/list-profiles", {
            "statuses": statuses,
            "min_quality_score": min_quality_score,
            "lead_ids": lead_ids or [],
        })
        records = index.get("items", []) if isinstance(index, dict) else []
        pairs: list[tuple[dict, dict]] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            profile = self._artifact_json(str(item.get("artifact_key") or ""))
            lead_id = str(item.get("lead_id") or "")
            lead = self._d1_lead(lead_id)
            if profile is None or lead is None:
                logger.warning("ResearchReader: D1/R2 profile is missing its lead %s", lead_id)
                continue
            pairs.append((profile, lead))
        logger.info("ResearchReader: loaded %d durable research/lead pairs", len(pairs))
        return pairs

    def _d1_lead(self, lead_id: str) -> Optional[dict]:
        if not lead_id:
            return None
        value = self._bridge_json("lead-pipeline/list-leads", {
            "stage": "scored", "lead_ids": [lead_id],
        })
        leads = value.get("leads", []) if isinstance(value, dict) else []
        return leads[0] if leads and isinstance(leads[0], dict) else None

    def _artifact_json(self, key: str) -> Optional[dict]:
        if not key:
            return None
        request = Request(f"{self._bridge_url}/v1/artifacts/{key}", method="GET")
        try:
            with urlopen(request, timeout=15) as response:  # nosec B310 Worker virtual host
                value = json.loads(response.read().decode())
        except Exception as exc:
            logger.error("ResearchReader: cannot load R2 profile %s — %s", key, exc)
            return None
        return value if isinstance(value, dict) else None

    def _bridge_json(self, operation: str, payload: dict) -> dict:
        request = Request(
            f"{self._bridge_url}/v1/{operation}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=15) as response:  # nosec B310 Worker virtual host
            value = json.loads(response.read().decode())
        if not isinstance(value, dict):
            raise RuntimeError("Worker storage bridge returned an invalid response")
        return value

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
