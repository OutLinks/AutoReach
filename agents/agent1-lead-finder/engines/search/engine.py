"""
Module 1 — Search Engine.

Scrapes public source pages by default, with external search APIs available as
an optional enhancement. Results are merged into a unified list of Lead
objects and pushed to Redis under `leads:raw`.
"""

from __future__ import annotations

import asyncio
import logging

from ...config import ServiceConfig
from ...models import Lead, SearchCriteria
from ...storage import RedisStore
from ..base import BaseEngine
from .google_places import GooglePlacesAdapter
from .tavily import TavilyAdapter
from .web_scraper import WebScraperAdapter

logger = logging.getLogger(__name__)


class SearchEngine(BaseEngine):
    """
    Module 1: finds raw leads from public web pages and, when explicitly
    enabled, external search APIs.

    Each enabled API runs as a concurrent task. Results are merged and
    stored in Redis `job:{job_id}:leads:raw` for the next pipeline stage.
    """

    def __init__(self, config: ServiceConfig, store: RedisStore) -> None:
        super().__init__(concurrency=config.concurrency)
        self._config = config
        self._store = store

    async def run(self, criteria: SearchCriteria, job_id: str) -> list[Lead]:
        """
        Scrape configured/user-supplied public source pages and optionally run
        enabled discovery APIs. The scraper is the default and does not need
        an API key.
        """
        enabled_apis = self._config.enabled_search_apis()
        source_urls = list(dict.fromkeys(
            [*self._config.web_scraper_seed_urls, *criteria.source_urls]
        ))
        source_count = int(self._config.web_scraper_enabled and bool(source_urls))
        task_count = source_count + len(enabled_apis)
        if not task_count:
            logger.warning(
                "No scrape source URLs were supplied and no optional discovery APIs are enabled. "
                "Add public company/directory URLs to the prompt or LEAD_FINDER_SOURCE_URLS."
            )
            await self._store.push_leads(job_id, "raw", [])
            return []

        per_source = max(1, criteria.max_results // task_count)

        tasks: list[asyncio.Task] = []
        active_sources: list[str] = []
        if source_count:
            adapter = WebScraperAdapter()
            tasks.append(asyncio.create_task(
                self._guarded(adapter.search(criteria, source_urls, per_source)),
                name="web_scraper",
            ))
            active_sources.append("web_scraper")
        if "google_places" in enabled_apis:
            adapter = GooglePlacesAdapter(self._config.google_places.api_key)
            tasks.append(asyncio.create_task(
                self._guarded(adapter.search(criteria, per_source)),
                name="google_places",
            ))
            active_sources.append("google_places")
        if "tavily" in enabled_apis:
            adapter = TavilyAdapter(self._config.tavily.api_key)
            tasks.append(asyncio.create_task(
                self._guarded(adapter.search(criteria, per_source)),
                name="tavily",
            ))
            active_sources.append("tavily")

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
            "SearchEngine: %d total leads found  (sources=%s)",
            len(all_leads), active_sources,
        )
        return all_leads
