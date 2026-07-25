"""
Agent 2: Research Analyst

Reads qualifying leads from Agent 1's JSONL output and produces deep
ResearchProfile records per lead. Three-layer pipeline:

  Layer 1 — Data Collection : website, LinkedIn, news, social (concurrent)
  Layer 2 — AI Analysis     : company, pain points, personal, angle, score
  Layer 3 — Output          : validate + write to JSONL with lead_id FK

Usage:
    from agents.agent2_research_analyst.agent import ResearchAgent
    from agents.agent2_research_analyst.config import ServiceConfig

    agent = ResearchAgent(ServiceConfig.from_env())
    job = await agent.run()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .config import ServiceConfig
from .models import (
    RawResearchData,
    ResearchJob,
    ResearchProfile,
)
from .layers.collection.collector import DataCollector
from .layers.analysis.analyzer import AnalysisLayer
from .layers.output.writer import OutputWriter
from .storage.lead_reader import LeadReader
from .storage.research_store import ResearchStore

logger = logging.getLogger(__name__)


class ResearchAgent:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._collector = DataCollector(config)
        self._analysis = AnalysisLayer(config)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(
        self,
        job_id: Optional[str] = None,
        lead_ids: Optional[list[str]] = None,
        min_grade: str = "B",
        leads_file: Optional[Path] = None,
    ) -> ResearchJob:
        """
        Research qualifying leads and write profiles.

        Args:
            job_id:     Optional job identifier (auto-generated if None).
            lead_ids:   Specific lead IDs to research (all qualifying if None).
            min_grade:  Minimum Agent 1 grade to process ("A", "B", "C").
            leads_file: Specific JSONL file from Agent 1 (all files if None).
        """
        job_id = job_id or str(uuid4())
        logger.info("ResearchAgent: starting job %s", job_id)

        # 1. Load leads
        leads = self._load_leads(lead_ids, min_grade, leads_file)
        if not leads:
            logger.warning("ResearchAgent: no qualifying leads found")
            return ResearchJob(
                id=job_id, status="complete", total=0, completed_at=datetime.utcnow()
            )

        job = ResearchJob(id=job_id, lead_ids=[l["id"] for l in leads], total=len(leads))
        job.status = "in_progress"
        logger.info("ResearchAgent: researching %d leads", len(leads))

        # 2. Set up output
        store = ResearchStore(job_id=job_id)
        writer = OutputWriter(store)

        # 3. Process leads with concurrency limit
        sem = asyncio.Semaphore(self._config.concurrency)

        async def process_one(lead: dict) -> None:
            async with sem:
                await self._process_lead(lead, job, writer)

        await asyncio.gather(*[process_one(lead) for lead in leads])

        # 4. Finalize
        job.status = "complete"
        job.completed_at = datetime.utcnow()
        logger.info(
            "ResearchAgent: done — %d complete, %d partial, %d failed (wrote to %s)",
            job.completed,
            job.partial,
            job.failed,
            store.path,
        )
        return job

    # ── Internal pipeline steps ────────────────────────────────────────────────

    async def _process_lead(
        self, lead: dict, job: ResearchJob, writer: OutputWriter
    ) -> None:
        lead_id = lead.get("id", str(uuid4()))
        profile = ResearchProfile(lead_id=lead_id, status="in_progress")

        try:
            # Layer 1: Data Collection
            raw_data = await self._collector.collect(lead)
            profile.raw_data = raw_data
            profile.research_completeness = raw_data.completeness
            profile.sources_used = raw_data.sources_succeeded

            # Layer 2: AI Analysis
            await self._analysis.analyze(lead, raw_data, profile)

            # Layer 3: Output
            writer.write(profile, job)

        except Exception as exc:
            logger.error(
                "ResearchAgent: unexpected error for lead %s — %s", lead_id, exc
            )
            profile.status = "failed"
            profile.error = str(exc)
            job.failed += 1

    def _load_leads(
        self,
        lead_ids: Optional[list[str]],
        min_grade: str,
        leads_file: Optional[Path],
    ) -> list[dict]:
        reader = LeadReader()

        if lead_ids:
            # The orchestrator already selected these exact records. Do not
            # silently discard them with Agent 1's sales-oriented A/B grade
            # filter (job/company research often has no contact email yet).
            leads = [
                lead
                for lead_id in lead_ids
                if (lead := reader.read_by_id(lead_id)) is not None
            ]
        elif leads_file:
            leads = reader.read_file(leads_file, min_grade=min_grade)
        else:
            leads = reader.read_all(min_grade=min_grade)

        return leads
