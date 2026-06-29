"""
Module 2 — Enrich Engine.

Enriches verified leads with lower-cost company, contact, technology, and
domain intelligence APIs.

Leads with invalid or missing emails are still enriched at the company level.
"""

from __future__ import annotations

import asyncio
import logging

from ...config import ServiceConfig
from ...models import Lead
from ...storage import RedisStore
from ..base import BaseEngine
from .crunchbase import CrunchbaseAdapter
from .domain_intel import SecurityTrailsAdapter, WhoisXmlAdapter
from .hunter_domain import HunterDomainSearchAdapter
from .wappalyzer import WappalyzerAdapter

logger = logging.getLogger(__name__)


class EnrichEngine(BaseEngine):
    """
    Module 2: enriches leads with company and domain intelligence.

    For each lead, all configured enrichers run in parallel where enabled.
    Results are merged into the existing Lead — existing fields are never
    overwritten, so search-stage data takes priority.
    """

    def __init__(self, config: ServiceConfig, store: RedisStore) -> None:
        super().__init__(concurrency=config.concurrency)
        self._config = config
        self._store = store

        self._hunter = (
            HunterDomainSearchAdapter(config.hunter.api_key)
            if config.hunter.is_ready()
            else None
        )
        self._wappalyzer = (
            WappalyzerAdapter(config.wappalyzer.api_key)
            if config.wappalyzer.is_ready()
            else None
        )
        self._crunchbase = (
            CrunchbaseAdapter(config.crunchbase.api_key)
            if config.crunchbase.is_ready()
            else None
        )
        self._whoisxml = (
            WhoisXmlAdapter(config.whoisxml.api_key)
            if config.whoisxml.is_ready()
            else None
        )
        self._securitytrails = (
            SecurityTrailsAdapter(config.securitytrails.api_key)
            if config.securitytrails.is_ready()
            else None
        )

    async def run(self, leads: list[Lead], job_id: str) -> list[Lead]:
        if not leads:
            return []

        if not self._enrichers:
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

        for enricher in self._enrichers:
            enrich_tasks.append(self._retry(enricher.enrich, lead))

        if enrich_tasks:
            # Run all enrichers concurrently; each writes to the same lead dict
            await asyncio.gather(*enrich_tasks, return_exceptions=True)

        lead.stage = "enriched"
        lead.touch()
        return lead

    @property
    def _enrichers(self) -> list:
        return [
            enricher
            for enricher in [
                self._hunter,
                self._wappalyzer,
                self._crunchbase,
                self._whoisxml,
                self._securitytrails,
            ]
            if enricher is not None
        ]
