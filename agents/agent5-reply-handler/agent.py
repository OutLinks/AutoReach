"""
Agent 5: Reply Handler

The intelligence layer that closes the loop. Reads replies surfaced by Agent 4,
understands intent/sentiment/urgency, decides whether the AI can handle it or a
human must, drafts and sends the response, books meetings, and keeps a full
conversation memory — escalating the moment it's unsure or the stakes are high.

Four-layer pipeline:
  Layer 1 — Input         : read reply → parse → attach conversation context
  Layer 2 — Understanding : intent → sentiment → urgency → decision
  Layer 3 — Action        : auto-reply / meeting / objection / human handoff
  Layer 4 — Output        : update memory, send reply, notify human, stop sequence

Usage:
    from agents.agent5_reply_handler.agent import ReplyHandlerAgent
    from agents.agent5_reply_handler.config import ServiceConfig

    agent = ReplyHandlerAgent(ServiceConfig.from_env())
    job = await agent.run()                  # drain Agent 4's reply hand-offs
    await agent.handle_payload({...})        # or handle one reply directly (webhook)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from .config import ServiceConfig
from .models import ACTION_HUMAN_HANDOFF, IncomingReply, ReplyJob
from .storage.conversation_store import ConversationStore
from .layers.input.input_layer import InputLayer
from .layers.understanding.understanding_layer import UnderstandingLayer
from .layers.action.action_layer import ActionLayer
from .layers.output.output_layer import OutputLayer

logger = logging.getLogger(__name__)


class ReplyHandlerAgent:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._store = ConversationStore(config.db_path)

        self._input = InputLayer(config, self._store)
        self._understanding = UnderstandingLayer(config)
        self._action = ActionLayer(config)
        self._output = OutputLayer(config, self._store)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(self, job_id: Optional[str] = None) -> ReplyJob:
        """Handle every pending reply hand-off from Agent 4."""
        job_id = job_id or str(uuid4())
        logger.info("ReplyHandlerAgent: starting job %s", job_id)

        replies = self._input.collect()
        job = ReplyJob(id=job_id, total=len(replies), status="in_progress")
        self._store.upsert_job(job)

        if not replies:
            return self._finalize(job)

        sem = asyncio.Semaphore(self._config.concurrency)

        async def worker(reply: IncomingReply) -> None:
            async with sem:
                await self._process(reply, job)

        await asyncio.gather(*(worker(r) for r in replies))
        return self._finalize(job)

    async def handle_payload(self, payload: dict) -> ReplyJob:
        """Handle a single reply delivered directly (e.g. a provider webhook)."""
        job = ReplyJob(id=str(uuid4()), total=1, status="in_progress")
        reply = self._input.prepare_payload(payload)
        await self._process(reply, job)
        return self._finalize(job)

    # ── Pipeline ───────────────────────────────────────────────────────────────

    async def _process(self, reply: IncomingReply, job: ReplyJob) -> None:
        try:
            # Layer 2: understand + decide.
            understanding, decision = await self._understanding.analyze(reply)

            # Build a transcript (prior messages + this reply) for any hand-off.
            prior = self._output.conversation_excerpt(reply.lead_id)
            excerpt = f"{prior}\nLead: {reply.clean_body or reply.raw_body}".strip()

            # Layer 3: act.
            result = await self._action.act(reply, understanding, decision, excerpt)

            # Layer 4: commit side effects.
            summary = await self._output.commit(reply, understanding, result)

            # Roll up counters.
            if result.action_type == ACTION_HUMAN_HANDOFF:
                job.escalated += 1
            else:
                job.handled += 1
            if summary.get("reply_sent"):
                job.replies_sent += 1
            if summary.get("meeting"):
                job.meetings_booked += 1

            logger.info(
                "ReplyHandlerAgent: lead %s — intent=%s action=%s (reply_sent=%s)",
                reply.lead_id, understanding.intent.intent, result.action_type,
                summary.get("reply_sent"),
            )
        except Exception as exc:
            logger.error("ReplyHandlerAgent: error handling lead %s — %s", reply.lead_id, exc)
            job.skipped += 1

    # ── Internal ───────────────────────────────────────────────────────────────

    def _finalize(self, job: ReplyJob) -> ReplyJob:
        job.status = "complete"
        job.completed_at = datetime.utcnow()
        self._store.upsert_job(job)
        logger.info(
            "ReplyHandlerAgent: job %s done — handled=%d escalated=%d replies=%d meetings=%d skipped=%d",
            job.id, job.handled, job.escalated, job.replies_sent, job.meetings_booked, job.skipped,
        )
        return job
