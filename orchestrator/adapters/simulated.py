"""
Simulated adapters.

Deterministic stand-ins for the five agents so the whole pipeline runs (and is
testable) without API keys, LLMs, or network. Branch outcomes are derived from a
stable hash of the lead id, so a given lead always takes the same path — this is
what makes dry-runs reproducible and the orchestrator verifiable end-to-end.

These are the default adapters when OrchestratorConfig.simulate is True.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from ..config import OrchestratorConfig
from ..models import DISCOVERED, PipelineLead, SENT, FOLLOWING_UP, StageResult
from ..state_machine import FIND, FOLLOWUP, REPLY, RESEARCH, SEND, WRITE, Stage
from ..store import OrchestratorStore
from .base import AgentAdapter, StageContext

logger = logging.getLogger(__name__)


def _bucket(lead_id: str, mod: int) -> int:
    """Stable 0..mod-1 bucket for a lead id (reproducible branching)."""
    return int(hashlib.md5(lead_id.encode()).hexdigest(), 16) % mod


class SimulatedAdapter(AgentAdapter):
    def __init__(self, stage: Stage) -> None:
        super().__init__(stage)

    async def execute(self, ctx: StageContext) -> StageResult:
        handler = {
            FIND.name: self._find,
            RESEARCH.name: self._research,
            WRITE.name: self._write,
            SEND.name: self._send,
            FOLLOWUP.name: self._followup,
            REPLY.name: self._reply,
        }[self.stage.name]
        return handler(ctx)

    # ── find: create new leads ─────────────────────────────────────────────────

    def _find(self, ctx: StageContext) -> StageResult:
        n = ctx.config.volume.leads_per_day
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        # Deterministic ids (offset by what already exists) keep dry-runs
        # reproducible while letting repeated finds add fresh, non-colliding leads.
        offset = len(ctx.store.all_leads())
        for i in range(offset, offset + n):
            lead = PipelineLead(
                id=f"sim-lead-{i:05d}",
                state=DISCOVERED,
                email=f"lead{i}@example-{_bucket(str(i), 9999)}.com",
                company=f"Company {i}",
                industry=ctx.config.targeting.industries[i % len(ctx.config.targeting.industries)],
                quality_score=0.5 + (_bucket(f"q{i}", 50) / 100.0),     # 0.5–0.99
                company_size_score=_bucket(f"s{i}", 100) / 100.0,
                industry_fit_score=_bucket(f"f{i}", 100) / 100.0,
                recency_score=1.0,
                discovered_at=ctx.now,
                source_job=ctx.config.targeting.search_prompt[:24],
            )
            ctx.store.upsert_lead(lead)
            result.new_lead_ids.append(lead.id)
        result.processed = result.succeeded = n
        logger.info("SimulatedAdapter[find]: created %d leads", n)
        return result

    # ── research: DISCOVERED → RESEARCHED ───────────────────────────────────────

    def _research(self, ctx: StageContext) -> StageResult:
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            # 1 in 12 research profiles comes back "incomplete" (low quality lead).
            incomplete = _bucket(f"research:{lid}", 12) == 0
            result.outcomes[lid] = "incomplete" if incomplete else "ok"
            if not incomplete:
                result.advanced_ids.append(lid)
        result.processed = len(ctx.lead_ids)
        result.succeeded = len(result.advanced_ids)
        result.failed = result.processed - result.succeeded
        return result

    # ── write: RESEARCHED → READY (quality-gated) ───────────────────────────────

    def _write(self, ctx: StageContext) -> StageResult:
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            score = 0.55 + _bucket(f"email:{lid}", 45) / 100.0    # 0.55–0.99
            result.outcomes[lid] = f"email_score={score:.2f}"
            result.advanced_ids.append(lid)
        result.processed = result.succeeded = len(ctx.lead_ids)
        return result

    # ── send: READY → SENT (bounce-gated) ───────────────────────────────────────

    def _send(self, ctx: StageContext) -> StageResult:
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            bounced = _bucket(f"bounce:{lid}", 20) == 0           # ~5% bounce
            result.outcomes[lid] = "bounced" if bounced else "sent"
            if not bounced:
                result.advanced_ids.append(lid)
            else:
                result.failed += 1
        result.processed = len(ctx.lead_ids)
        result.succeeded = len(result.advanced_ids)
        return result

    # ── followup: SENT → FOLLOWING_UP / NO_REPLY ────────────────────────────────

    def _followup(self, ctx: StageContext) -> StageResult:
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            lead = ctx.store.get_lead(lid)
            steps = (lead.metadata.get("followup_steps", 0) if lead else 0) + 1
            if steps >= 3:
                result.outcomes[lid] = "exhausted"     # breakup sent → NO_REPLY
            else:
                result.outcomes[lid] = "continued"
                if lead:
                    lead.metadata["followup_steps"] = steps
                    ctx.store.upsert_lead(lead)
            result.advanced_ids.append(lid)
        result.processed = result.succeeded = len(ctx.lead_ids)
        return result

    # ── reply: REPLIED → HANDLED / MEETING_BOOKED / CLOSED / escalated ──────────

    def _reply(self, ctx: StageContext) -> StageResult:
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            b = _bucket(f"reply:{lid}", 10)
            if b < 3:
                outcome = "meeting_booked"
            elif b < 5:
                outcome = "escalated"
            elif b < 7:
                outcome = "closed"
            else:
                outcome = "handled"
            result.outcomes[lid] = outcome
            result.advanced_ids.append(lid)
        result.processed = result.succeeded = len(ctx.lead_ids)
        return result

    # ── ingest: simulate inbound replies for the reply stage ────────────────────

    async def ingest(
        self,
        store: OrchestratorStore,
        config: OrchestratorConfig,
        now: datetime | None = None,
    ) -> int:
        """Flip a deterministic ~30% of awaiting leads to REPLIED before reply runs."""
        if self.stage.name != REPLY.name:
            return 0
        from ..models import REPLIED
        now = now or datetime.now(timezone.utc)
        flipped = 0
        for state in (SENT, FOLLOWING_UP):
            for lead in store.leads_in_state(state):
                if _bucket(f"inbound:{lead.id}", 10) < 3:
                    prev = lead.state
                    lead.state = REPLIED
                    lead.replied_at = now
                    lead.state_entered_at = now
                    store.upsert_lead(lead)
                    store.log_event(lead.id, prev, REPLIED, "simulated inbound reply")
                    flipped += 1
        if flipped:
            logger.info("SimulatedAdapter[reply]: %d leads replied", flipped)
        return flipped
