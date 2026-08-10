"""
Persists ResearchProfile records to JSONL.

Each file is named research_{job_id[:8]}_{timestamp}.jsonl.
The lead_id field in each record is the FK linking back to Agent 1's leads.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from core.runtime_paths import agent_output_dir

from ..models import ResearchProfile

logger = logging.getLogger(__name__)

_OUTPUT_DIR = agent_output_dir("agent2-research-analyst")


class ResearchStore:
    def __init__(self, job_id: str, output_dir: Optional[Path] = None) -> None:
        self._job_id = job_id
        self._r2 = os.environ.get("AUTOREACH_ARTIFACT_BACKEND", "local").lower() == "r2"
        self._records: list[dict] = []
        self._dir = output_dir or _OUTPUT_DIR
        if not self._r2:
            self._dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"research_{job_id[:8]}_{ts}.jsonl"
        self._path = self._dir / filename
        self._written = 0

        logger.info("ResearchStore: writing to %s", "R2" if self._r2 else self._path)

    # ── Public API ─────────────────────────────────────────────────────────────

    def write(self, profile: ResearchProfile) -> None:
        """Append one ResearchProfile as a JSON line."""
        try:
            record = profile.model_dump(mode="json")
            if self._r2:
                key = f"research/{self._job_id}/{profile.lead_id}.json"
                request = Request(
                    f"http://autoreach.storage/v1/artifacts/{key}",
                    data=json.dumps(record).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="PUT",
                )
                with urlopen(request, timeout=15):  # nosec B310 - Worker virtual hostname
                    pass
                quality_score = profile.quality_score.score if profile.quality_score else 0.0
                index_request = Request(
                    "http://autoreach.storage/v1/research-index/upsert-profile",
                    data=json.dumps({
                        "id": profile.id,
                        "lead_id": profile.lead_id,
                        "job_id": self._job_id,
                        "artifact_key": key,
                        "status": profile.status,
                        "quality_score": quality_score,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(index_request, timeout=15):  # nosec B310 - Worker virtual hostname
                    pass
            else:
                with open(self._path, "a") as f:
                    f.write(json.dumps(record) + "\n")
            self._records.append(record)
            self._written += 1
        except Exception as exc:
            logger.error(
                "ResearchStore: failed to write profile for lead %s — %s",
                profile.lead_id,
                exc,
            )

    def write_batch(self, profiles: list[ResearchProfile]) -> None:
        for profile in profiles:
            self.write(profile)

    @property
    def count(self) -> int:
        return self._written

    @property
    def path(self) -> Path:
        return self._path

    @property
    def records(self) -> list[dict]:
        return list(self._records)
