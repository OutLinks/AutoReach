"""
Agent 1: Lead Finder — main entry point.

Wires together the LLM planner, four pipeline engines, Redis storage,
and the final DB writer into a single `run()` call.

Pipeline:
  prompt → [LLM] → SearchCriteria
         → SearchEngine   → raw leads in Redis
         → ScoreDedupeEngine.dedupe()  → unique leads in Redis
         → EnrichEngine   → enriched leads in Redis
         → VerifyEngine   → verified leads in Redis
         → ScoreDedupeEngine.score()   → scored leads in Redis
         → DBWriter       → final output file / database
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from uuid import uuid4

from core.model_selection import Message, get_model

from .config import ServiceConfig
from .engines.enrich.engine import EnrichEngine
from .engines.score_dedupe.engine import ScoreDedupeEngine
from .engines.search.engine import SearchEngine
from .engines.verify.engine import VerifyEngine
from .models import SearchCriteria, SearchJob
from .prompt import build_system_prompt
from .storage import DBWriter, RedisStore

logger = logging.getLogger(__name__)


class LeadFinderAgent:
    """
    Autonomous lead finder.

    Usage:
        config = ServiceConfig()
        config.tavily.enabled = False  # run only against explicitly supplied URLs

        agent = LeadFinderAgent(config)
        job = await agent.run("Find 50 real estate founders in San Francisco")

        print(f"Found {job.total_written} leads  ({job.status})")
    """

    def __init__(self, config: ServiceConfig | None = None) -> None:
        self._config = config or ServiceConfig()
        self._store = RedisStore(self._config.redis_url, self._config.redis_ttl)

        # Scrape-first discovery plus optional API enrichment/verification.
        self._search = SearchEngine(self._config, self._store)
        self._verify = VerifyEngine(self._config, self._store)
        self._enrich = EnrichEngine(self._config, self._store)
        self._score_dedupe = ScoreDedupeEngine(self._config, self._store)

        self._db_writer = DBWriter()
        self._adapter = get_model(self._config.model)

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, prompt: str, job_id: str | None = None) -> SearchJob:
        """
        Execute the full lead-finding pipeline for the given natural-language prompt.

        Returns a SearchJob with pipeline counters and final status.
        Raises on unrecoverable errors (after persisting `status="failed"` to Redis).
        """
        job_id = job_id or str(uuid4())
        job = SearchJob(id=job_id, prompt=prompt, status="pending")
        await self._store.save_job(job)

        logger.info("Job %s started — prompt: %r", job_id[:8], prompt[:80])

        try:
            # ── Phase 1: Parse prompt → SearchCriteria via LLM ──────────────
            logger.info("[%s] Phase 1: parsing prompt", job_id[:8])
            criteria = await self._parse_prompt(prompt)
            job.criteria = criteria
            job.status = "searching"
            await self._store.save_job(job)

            logger.info(
                "[%s] Criteria: industries=%s  titles=%s  locations=%s  max=%d  apis=%s",
                job_id[:8], criteria.industries, criteria.job_titles,
                criteria.locations, criteria.max_results, criteria.api_priorities,
            )

            # ── Phase 2: Search — find raw leads ────────────────────────────
            logger.info("[%s] Phase 2: searching", job_id[:8])
            raw_leads = await self._search.run(criteria, job_id)
            job.total_found = len(raw_leads)
            await self._store.save_job(job)

            # ── Phase 3: Deduplicate ─────────────────────────────────────────
            logger.info("[%s] Phase 3: deduplicating %d leads", job_id[:8], len(raw_leads))
            unique_leads = await self._score_dedupe.dedupe(raw_leads, job_id)
            job.total_unique = len(unique_leads)
            await self._store.save_job(job)

            # ── Phase 4: Enrich ──────────────────────────────────────────────
            logger.info("[%s] Phase 4: enriching %d leads", job_id[:8], len(unique_leads))
            job.status = "enriching"
            await self._store.save_job(job)
            enriched_leads = await self._enrich.run(unique_leads, job_id)
            job.total_enriched = len(enriched_leads)
            await self._store.save_job(job)

            # ── Phase 5: Verify emails + websites ───────────────────────────
            logger.info("[%s] Phase 5: verifying %d leads", job_id[:8], len(enriched_leads))
            job.status = "verifying"
            await self._store.save_job(job)
            verified_leads = await self._verify.run(enriched_leads, job_id)
            job.total_verified = len(verified_leads)
            await self._store.save_job(job)

            # ── Phase 6: Score + sort ────────────────────────────────────────
            logger.info("[%s] Phase 6: scoring %d leads", job_id[:8], len(verified_leads))
            job.status = "scoring"
            await self._store.save_job(job)
            scored_leads = await self._score_dedupe.score(verified_leads, job_id)
            job.total_scored = len(scored_leads)
            await self._store.save_job(job)

            # ── Phase 7: Write to DB from Redis ─────────────────────────────
            logger.info("[%s] Phase 7: writing to DB", job_id[:8])
            written = await self._db_writer.write(scored_leads, job_id)
            job.total_written = written

            job.status = "complete"
            job.completed_at = datetime.utcnow()
            await self._store.save_job(job)

            logger.info(
                "Job %s COMPLETE — found=%d unique=%d verified=%d enriched=%d "
                "scored=%d written=%d",
                job_id[:8], job.total_found, job.total_unique, job.total_verified,
                job.total_enriched, job.total_scored, job.total_written,
            )

        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            await self._store.save_job(job)
            logger.exception("Job %s FAILED: %s", job_id[:8], exc)
            raise

        return job

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _parse_prompt(self, prompt: str) -> SearchCriteria:
        """
        Call the LLM to extract structured SearchCriteria from the user's prompt.

        The system prompt is built dynamically from the current ServiceConfig,
        so the LLM only sees and suggests APIs that are actually enabled.
        """
        system_prompt = build_system_prompt(self._config)

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=prompt),
        ]

        response = await self._adapter.complete(self._config.model, messages)
        content = (response.content or "").strip()

        if not content:
            logger.warning("LLM returned empty response — using default criteria")
            return SearchCriteria(
                max_results=self._config.max_leads_per_run,
                source_urls=self._extract_urls(prompt),
            )

        # Extract JSON — handle both raw JSON and markdown-fenced JSON
        json_text = self._extract_json(content)

        try:
            data = json.loads(json_text)
            criteria = SearchCriteria.model_validate(data)
            # Do not let the planner invent crawl targets. Only URLs explicitly
            # supplied by the user are eligible; persistent source URLs belong
            # in LEAD_FINDER_SOURCE_URLS.
            criteria.source_urls = self._extract_urls(prompt)
            # Clamp max_results to the configured limit
            criteria.max_results = min(criteria.max_results, self._config.max_leads_per_run)
            # Only keep api_priorities that are actually enabled
            enabled = (
                self._config.enabled_search_apis()
                + self._config.enabled_enrich_apis()
                + self._config.enabled_verify_apis()
            )
            criteria.api_priorities = [
                api for api in criteria.api_priorities if api in enabled
            ]
            return criteria
        except Exception as exc:
            logger.warning("Failed to parse LLM JSON (%s) — using defaults", exc)
            return SearchCriteria(
                max_results=self._config.max_leads_per_run,
                source_urls=self._extract_urls(prompt),
            )

    @staticmethod
    def _extract_json(text: str) -> str:
        """Pull JSON out of LLM output that may be wrapped in markdown fences."""
        # Try ```json ... ``` first
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            return fenced.group(1)
        # Try bare JSON object
        bare = re.search(r"\{.*\}", text, re.DOTALL)
        if bare:
            return bare.group(0)
        return text

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        """Return distinct HTTP(S) URLs explicitly present in a user prompt."""
        urls = re.findall(r"https?://[^\s<>()\[\]{}\"']+", text)
        return list(dict.fromkeys(url.rstrip(".,;:!?)") for url in urls))
