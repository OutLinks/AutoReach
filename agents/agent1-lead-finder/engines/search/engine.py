"""
Module 1 — Search Engine.

Calls all enabled search APIs in parallel, merges results into a unified
list of Lead objects, and pushes them to Redis under `leads:raw`.
"""

from __future__ import annotations

import asyncio
import logging

from ...config import ServiceConfig
from ...models import Lead, SearchCriteria
from ...storage import RedisStore
from ..base import BaseEngine
from .apollo import ApolloAdapter
from .producthunt import ProductHuntAdapter

logger = logging.getLogger(__name__)


class SearchEngine(BaseEngine):
    """
    Module 1: finds raw leads using enabled search APIs.

    Each enabled API runs as a concurrent task. Results are merged and
    stored in Redis `job:{job_id}:leads:raw` for the next pipeline stage.
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

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_leads: list[Lead] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Search task raised: %s", result)
            elif isinstance(result, list):
                all_leads.extend(result)

        # Trim to requested max
        all_leads = all_leads[: criteria.max_results]

        # Persist to Redis
        await self._store.push_leads(job_id, "raw", all_leads)

        logger.info(
            "SearchEngine: %d total leads found  (apis=%s)",
            len(all_leads), enabled,
        )
        return all_leads
