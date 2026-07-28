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
            raise ValueError(
                "No web discovery source is configured. Add a Tavily API key "
                "with PATCH /v1/settings, configure lead_finder_source_urls, "
                "or include a public company or directory URL in the search query."
            )

        tasks: list[asyncio.Task] = []
        task_names: list[str] = []
        scraper = WebScraperAdapter()
        if source_count:
            tasks.append(asyncio.create_task(
                self._guarded(
                    scraper.search(criteria, source_urls, criteria.max_results)
                ),
                name="web_scraper",
            ))
            task_names.append("web_scraper")
        if "tavily" in enabled_apis:
            adapter = TavilyAdapter(self._config.tavily.api_key)
            candidate_limit = min(max(criteria.max_results * 5, 10), 20)
            tasks.append(asyncio.create_task(
                self._guarded(adapter.search(criteria, candidate_limit)),
                name="tavily",
            ))
            task_names.append("tavily")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_leads: list[Lead] = []
        for task_name, result in zip(task_names, results):
            if isinstance(result, Exception):
                logger.error("%s task raised: %s", task_name, result)
            elif task_name == "web_scraper" and isinstance(result, list):
                all_leads.extend(result)
            elif task_name == "tavily" and isinstance(result, list):
                candidate_urls = [
                    lead.company_website
                    for lead in result
                    if isinstance(lead, Lead) and lead.company_website
                ]
                scraped = await self._guarded(
                    scraper.scrape_companies(
                        criteria,
                        candidate_urls,
                        criteria.max_results,
                    )
                )
                for lead in scraped:
                    lead.merge_source("tavily")
                all_leads.extend(scraped)

        all_leads = self._deduplicate(all_leads)[: criteria.max_results]

        # Persist to Redis
        await self._store.push_leads(job_id, "raw", all_leads)

        logger.info(
            "SearchEngine: %d total leads found  (sources=%s)",
            len(all_leads), task_names,
        )
        return all_leads

    @staticmethod
    def _deduplicate(leads: list[Lead]) -> list[Lead]:
        unique: list[Lead] = []
        seen: set[str] = set()
        for lead in leads:
            if not lead.email or not lead.company_description:
                continue
            key = (lead.company_domain or lead.email).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(lead)
        return unique
