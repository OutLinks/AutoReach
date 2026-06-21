"""
Module 1 — Search Engine.

Runs in two stages:
  1. Discovery — criteria-driven sources (Apollo, ProductHunt, Hunter) run in
     parallel and produce leads, many carrying a company domain.
  2. Domain-seeded — Snov searches the domains surfaced in stage 1 (it cannot
     discover companies on its own).

Results are merged into a unified Lead list and pushed to Redis `leads:raw`.
"""

from __future__ import annotations

import asyncio
import logging

from ...config import ServiceConfig
from ...models import Lead, SearchCriteria
from ...storage import RedisStore
from ..base import BaseEngine
from .apollo import ApolloAdapter
from .hunter import HunterAdapter
from .producthunt import ProductHuntAdapter
from .snov import SnovAdapter

logger = logging.getLogger(__name__)


class SearchEngine(BaseEngine):
    """
    Module 1: finds raw leads using enabled search APIs.

    Discovery adapters run as concurrent tasks; Snov then runs against the
    domains they surfaced. Results are merged and stored in Redis
    `job:{job_id}:leads:raw` for the next pipeline stage.
    """

    def __init__(self, config: ServiceConfig, store: RedisStore) -> None:
        super().__init__(concurrency=config.concurrency)
        self._config = config
        self._store = store

    async def run(self, criteria: SearchCriteria, job_id: str) -> list[Lead]:
        """
        Execute all enabled search APIs and return the merged lead list.
        Each API gets an equal share of `max_results`.
        """
        enabled = self._config.enabled_search_apis()
        if not enabled:
            logger.warning("No search APIs are enabled — returning empty results")
            return []

        # Split budget evenly across active APIs
        per_api = max(10, criteria.max_results // len(enabled))

        # ── Stage 1: discovery (criteria-driven sources) ────────────────────
        tasks: list[asyncio.Task] = []
        if "apollo" in enabled:
            adapter = ApolloAdapter(self._config.apollo.api_key)
            tasks.append(asyncio.create_task(
                self._guarded(adapter.search(criteria, per_api)),
                name="apollo",
            ))
        if "producthunt" in enabled:
            adapter_ph = ProductHuntAdapter(self._config.producthunt.api_key)
            tasks.append(asyncio.create_task(
                self._guarded(adapter_ph.search(criteria, per_api)),
                name="producthunt",
            ))
        if "hunter" in enabled:
            adapter_h = HunterAdapter(self._config.hunter.api_key)
            tasks.append(asyncio.create_task(
                self._guarded(adapter_h.search(criteria, per_api)),
                name="hunter",
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_leads: list[Lead] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Search task raised: %s", result)
            elif isinstance(result, list):
                all_leads.extend(result)

        # ── Stage 2: domain-seeded sources (Snov) ───────────────────────────
        if "snov" in enabled:
            domains = list({
                lead.company_domain for lead in all_leads if lead.company_domain
            })
            if domains:
                snov = SnovAdapter(self._config.snov.user_id, self._config.snov.secret)
                try:
                    snov_leads = await self._guarded(
                        snov.search_by_domains(domains, criteria, per_api)
                    )
                    if isinstance(snov_leads, list):
                        all_leads.extend(snov_leads)
                except Exception as exc:
                    logger.error("Snov search raised: %s", exc)
            else:
                logger.info("Snov enabled but no seed domains discovered — skipping")

        # Trim to requested max
        all_leads = all_leads[: criteria.max_results]

        # Persist to Redis
        await self._store.push_leads(job_id, "raw", all_leads)

        logger.info(
            "SearchEngine: %d total leads found  (apis=%s)",
            len(all_leads), enabled,
        )
        return all_leads
