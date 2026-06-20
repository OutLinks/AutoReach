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

from ..models import ResearchProfile

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).parent.parent / "output"


class ResearchStore:
    def __init__(self, job_id: str, output_dir: Optional[Path] = None) -> None:
        self._dir = output_dir or _OUTPUT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"research_{job_id[:8]}_{ts}.jsonl"
        self._path = self._dir / filename
        self._written = 0

        logger.info("ResearchStore: writing to %s", self._path)

    # ── Public API ─────────────────────────────────────────────────────────────

    def write(self, profile: ResearchProfile) -> None:
        """Append one ResearchProfile as a JSON line."""
        try:
            record = profile.model_dump(mode="json")
            with open(self._path, "a") as f:
                f.write(json.dumps(record) + "\n")
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
