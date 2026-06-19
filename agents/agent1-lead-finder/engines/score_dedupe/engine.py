"""
Module 4 — Score + Dedupe Engine.

Handles two distinct pipeline stages:
  1. `dedupe()`  — called right after search, before any API credit spending
  2. `score()`   — called after enrichment, sorts leads by quality

Keeping dedupe and scoring in the same module makes sense because they share
the same data model (Lead) and both inform prioritisation.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import Lead
from ...storage import RedisStore
from ..base import BaseEngine
from .deduplicator import Deduplicator
from .scorer import LeadScorer

logger = logging.getLogger(__name__)


class ScoreDedupeEngine(BaseEngine):
    """
    Module 4: deduplication and lead scoring.

    dedupe() → removes seen leads, registers new ones in global Redis sets
    score()  → scores each lead 0–100 and sorts descending
    """

    def __init__(self, config: ServiceConfig, store: RedisStore) -> None:
        super().__init__(concurrency=config.concurrency)
        self._store = store
        self._deduplicator = Deduplicator(store)
        self._scorer = LeadScorer()

    async def dedupe(self, leads: list[Lead], job_id: str) -> list[Lead]:
        """
        Filter out duplicate leads and push unique ones to Redis.
        Called immediately after the SearchEngine.
        """
        unique = await self._deduplicator.dedupe(leads)
        await self._store.push_leads(job_id, "deduped", unique)
        return unique

    async def score(self, leads: list[Lead], job_id: str) -> list[Lead]:
        """
        Score all leads, sort by score descending, persist to Redis.
        Called after EnrichEngine.
        """
        scored = [self._scorer.score(lead) for lead in leads]

        # Sort: highest score first, then A > B > C > D within ties
        scored.sort(key=lambda l: l.lead_score or 0, reverse=True)

        await self._store.push_leads(job_id, "scored", scored)

        grade_dist = {g: 0 for g in ("A", "B", "C", "D")}
        for lead in scored:
            grade_dist[lead.lead_grade or "D"] += 1

        logger.info(
            "ScoreEngine: %d leads scored  grades=%s",
            len(scored), grade_dist,
        )
        return scored
