"""
Module 3 — Verify Engine.

Checks every unique lead's email and website before enrichment so we
don't waste enrichment API credits on dead contacts.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ...config import ServiceConfig
from ...models import Lead
from ...storage import RedisStore
from ..base import BaseEngine
from .hunter import HunterAdapter

logger = logging.getLogger(__name__)


class VerifyEngine(BaseEngine):
    """
    Module 3: validates emails (Hunter.io) and checks website reachability.

    Leads with email_status="invalid" are kept in the pipeline but flagged —
    the score engine penalises them heavily so they sort to the bottom.
    """

    def __init__(self, config: ServiceConfig, store: RedisStore) -> None:
        super().__init__(concurrency=config.concurrency)
        self._config = config
        self._store = store
        self._hunter = (
            HunterAdapter(config.hunter.api_key)
            if config.hunter.is_ready()
            else None
        )

    async def run(self, leads: list[Lead], job_id: str) -> list[Lead]:
        if not leads:
            return []

        tasks = [
            asyncio.create_task(self._guarded(self._process_one(lead)))
            for lead in leads
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        verified: list[Lead] = []
        for item in results:
            if isinstance(item, Exception):
                logger.error("Verify task raised: %s", item)
            elif item is not None:
                verified.append(item)

        await self._store.push_leads(job_id, "verified", verified)

        valid_count = sum(1 for l in verified if l.email_status == "valid")
        logger.info(
            "VerifyEngine: %d processed  %d valid emails",
            len(verified), valid_count,
        )
        return verified

    async def _process_one(self, lead: Lead) -> Lead:
        # Step 1: email verification
        if self._hunter:
            lead = await self._retry(self._hunter.verify, lead)

        # Step 2: website reachability (lightweight HEAD request)
        if lead.company_website:
            lead.website_reachable = await self._check_website(lead.company_website)

        lead.stage = "verified"
        lead.touch()
        return lead

    @staticmethod
    async def _check_website(url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                resp = await client.head(url)
                return resp.status_code < 500
        except Exception:
            return False
