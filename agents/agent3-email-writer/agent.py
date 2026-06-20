"""
Agent 3: Email Writer

Reads (research_profile, lead) pairs from Agents 1 & 2, writes personalized
emails through a 4-layer pipeline, and stores them in a relational SQLite DB.

Four-layer pipeline:
  Layer 1 — Input      : assemble context (research + template + brand voice + sender)
  Layer 2 — Writing    : subject → hook → body → CTA (sequential, each informs the next)
  Layer 3 — Quality    : personalization, spam, tone, length checks (parallel)
  Layer 4 — Output     : validate, optionally revise, persist to email DB

Usage:
    from agents.agent3_email_writer.agent import EmailWriterAgent
    from agents.agent3_email_writer.config import ServiceConfig

    agent = EmailWriterAgent(ServiceConfig.from_env())
    job = await agent.run()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from .config import ServiceConfig
from .models import EmailJob, WrittenEmail
from .layers.input.research_reader import ResearchReader
from .layers.input.assembler import InputAssembler
from .layers.input.brand_voice import BrandVoiceLoader
from .layers.input.sender_profile import SenderProfileLoader
from .layers.writing.writer import WritingLayer
from .layers.quality.checker import QualityLayer
from .layers.output.email_db import EmailDatabase

logger = logging.getLogger(__name__)


class EmailWriterAgent:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

        # Input Layer: load brand voice + sender profile at startup
        brand_voice = BrandVoiceLoader().load()
        sender = SenderProfileLoader().load()

        self._reader = ResearchReader()
        self._assembler = InputAssembler(brand_voice, sender)
        self._writing = WritingLayer(config)
        self._quality = QualityLayer(config)
        self._db = EmailDatabase(config.db_path)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(
        self,
        job_id: Optional[str] = None,
        lead_ids: Optional[list[str]] = None,
        min_quality_score: float = 5.0,
    ) -> EmailJob:
        """
        Write emails for all qualifying leads.

        Args:
            job_id:            Optional job identifier (auto-generated if None).
            lead_ids:          Specific lead IDs to target (all qualifying if None).
            min_quality_score: Minimum Agent 2 quality score (1–10) to include.
        """
        job_id = job_id or str(uuid4())
        logger.info("EmailWriterAgent: starting job %s", job_id)

        # 1. Load research/lead pairs
        if lead_ids:
            pairs = self._reader.read_by_lead_ids(lead_ids)
        else:
            pairs = self._reader.read_all(min_quality_score=min_quality_score)

        if not pairs:
            logger.warning("EmailWriterAgent: no qualifying leads found")
            return EmailJob(
                id=job_id, status="complete", total=0, completed_at=datetime.utcnow()
            )

        job = EmailJob(id=job_id, total=len(pairs), status="in_progress")
        self._db.upsert_job(job)
        logger.info("EmailWriterAgent: writing emails for %d leads", len(pairs))

        # 2. Process concurrently
        sem = asyncio.Semaphore(self._config.concurrency)

        async def process_one(research_profile: dict, lead: dict) -> None:
            async with sem:
                await self._process_lead(research_profile, lead, job, job_id)

        await asyncio.gather(
            *[process_one(rp, lead) for rp, lead in pairs]
        )

        # 3. Finalize
        job.status = "complete"
        job.completed_at = datetime.utcnow()
        self._db.upsert_job(job)

        logger.info(
            "EmailWriterAgent: done — %d written (%d passed quality, %d failed, %d skipped)",
            job.written,
            job.quality_passed,
            job.quality_failed,
            job.skipped,
        )
        return job

    # ── Internal pipeline ──────────────────────────────────────────────────────

    async def _process_lead(
        self,
        research_profile: dict,
        lead: dict,
        job: EmailJob,
        job_id: str,
    ) -> None:
        lead_id = lead.get("id", "")
        research_id = research_profile.get("id", "")

        try:
            # Layer 1: assemble input context
            ctx = self._assembler.assemble(research_profile, lead)

            # Layer 2: write the email
            draft = await self._writing.write(ctx)

            # Layer 3: quality check
            report = await self._quality.check(draft, ctx)

            # Optional revision if quality fails
            if not report.overall_passed and self._config.max_revision_attempts > 0:
                revised = await self._quality.revise(draft, ctx, report)
                if revised:
                    # Re-check the revision
                    revised_report = await self._quality.check(revised, ctx)
                    revised_report.revised_subject = revised.subject
                    revised_report.revised_body = revised.full_body
                    draft = revised
                    report = revised_report

            # Layer 4: persist to DB
            email = WrittenEmail(
                lead_id=lead_id,
                research_profile_id=research_id,
                subject=draft.subject,
                body=draft.full_body,
                hook=draft.hook,
                cta=draft.cta,
                lead_first_name=ctx.lead_first_name,
                lead_last_name=ctx.lead_last_name,
                lead_company=ctx.lead_company,
                sender_name=ctx.sender.full_name,
                sender_email=ctx.sender.email,
                tone=ctx.recommended_tone or ctx.brand_voice.tone,
                template_name=ctx.template.name,
                quality_score=report.overall_score,
                quality_passed=report.overall_passed,
                quality_report=report,
                status="approved" if report.overall_passed else "draft",
                job_id=job_id,
            )
            self._db.insert_email(email)

            job.written += 1
            if report.overall_passed:
                job.quality_passed += 1
            else:
                job.quality_failed += 1

            logger.info(
                "EmailWriterAgent: wrote email for %s %s (quality=%s, score=%.2f)",
                ctx.lead_first_name,
                ctx.lead_last_name,
                "PASS" if report.overall_passed else "FAIL",
                report.overall_score,
            )

        except Exception as exc:
            logger.error(
                "EmailWriterAgent: error for lead %s — %s", lead_id, exc
            )
            job.skipped += 1
