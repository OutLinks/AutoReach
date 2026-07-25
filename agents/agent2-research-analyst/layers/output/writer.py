"""
Output writer for Agent 2.

Validates and writes ResearchProfile records to the research store.
Invalid profiles are logged with the reason but not discarded from the
run stats — they count as "partial" so the caller can surface them.
"""

from __future__ import annotations

import logging
from typing import Optional

from ...models import ResearchProfile, ResearchJob
from .validator import validate
from ...storage.research_store import ResearchStore

logger = logging.getLogger(__name__)


class OutputWriter:
    def __init__(self, store: ResearchStore) -> None:
        self._store = store
        self._written = 0
        self._skipped = 0

    def write(self, profile: ResearchProfile, job: ResearchJob) -> None:
        """Validate and persist one profile. Updates job counters in place."""
        is_valid, reason = validate(profile)

        if is_valid:
            profile.mark_complete()
            self._store.write(profile)
            self._written += 1
            job.completed += 1
            logger.info(
                "OutputWriter: wrote profile for lead %s (score=%s)",
                profile.lead_id,
                f"{profile.quality_score.score:.1f}" if profile.quality_score else "n/a",
            )
        else:
            self._skipped += 1
            job.partial += 1
            profile.mark_partial(reason)
            self._store.write(profile)   # still write partial records for inspection
            logger.warning(
                "OutputWriter: partial profile for lead %s — %s", profile.lead_id, reason
            )

    @property
    def written(self) -> int:
        return self._written

    @property
    def skipped(self) -> int:
        return self._skipped
