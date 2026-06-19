"""
Module 2 — Enrich Engine.

Enriches verified leads with deeper company and person data from
ProxyCurl (LinkedIn) and Clearbit (company intelligence).

Leads with invalid emails are still enriched at the company level —
the company data remains useful even if we can't email this specific person.
"""

from __future__ import annotations

import asyncio
import logging

from ...config import ServiceConfig
from ...models import Lead
from ...storage import RedisStore
from ..base import BaseEngine
from .clearbit import ClearbitAdapter
from .proxycurl import ProxyCurlAdapter

logger = logging.getLogger(__name__)


class EnrichEngine(BaseEngine):
    """
    Module 2: enriches leads with LinkedIn and company intelligence.

    For each lead, both ProxyCurl and Clearbit run in parallel where enabled.
    Results are merged into the existing Lead — existing fields are never
    overwritten, so Apollo data (which is generally authoritative) takes priority.
    """

    def __init__(self, config: ServiceConfig, store: RedisStore) -> None:
        super().__init__(concurrency=config.concurrency)
        self._config = config
        self._store = store

        self._proxycurl = (
            ProxyCurlAdapter(config.proxycurl.api_key)
            if config.proxycurl.is_ready()
            else None
        )
        self._clearbit = (
            ClearbitAdapter(config.clearbit.api_key)
            if config.clearbit.is_ready()
            else None
        )

    async def run(self, leads: list[Lead], job_id: str) -> list[Lead]:
        if not leads:
            return []

        if not self._proxycurl and not self._clearbit:
            logger.warning(
                "No enrich APIs enabled — skipping enrichment, "
                "advancing all leads with raw data"
            )
            for lead in leads:
                lead.stage = "enriched"
            await self._store.push_leads(job_id, "enriched", leads)
            return leads

        tasks = [
            asyncio.create_task(self._guarded(self._enrich_one(lead)))
            for lead in leads
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched: list[Lead] = []
        for item in results:
            if isinstance(item, Exception):
                logger.error("Enrich task raised: %s", item)
            elif item is not None:
                enriched.append(item)

        await self._store.push_leads(job_id, "enriched", enriched)

        logger.info(
            "EnrichEngine: %d leads enriched  (apis=%s)",
            len(enriched), self._config.enabled_enrich_apis(),
        )
        return enriched

    async def _enrich_one(self, lead: Lead) -> Lead:
        """Run all enabled enrichers in parallel for a single lead."""
        enrich_tasks = []

        if self._proxycurl:
            enrich_tasks.append(self._retry(self._proxycurl.enrich_person, lead))
            if lead.company_linkedin_url:
                enrich_tasks.append(self._retry(self._proxycurl.enrich_company, lead))

        if self._clearbit:
            enrich_tasks.append(self._retry(self._clearbit.enrich_company, lead))
            if lead.email and lead.email_status != "invalid":
                enrich_tasks.append(self._retry(self._clearbit.enrich_person, lead))

        if enrich_tasks:
            # Run all enrichers concurrently; each writes to the same lead dict
            await asyncio.gather(*enrich_tasks, return_exceptions=True)

        lead.stage = "enriched"
        lead.touch()
        return lead
