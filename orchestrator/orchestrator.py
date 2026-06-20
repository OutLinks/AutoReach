"""
The Orchestrator engine.

The CEO of the operation. It owns no agent logic — it triggers stages, routes
leads between them, monitors health, decides branches, optimizes, and reports.

Key entry points:
  run_find()           — trigger Agent 1 to create a fresh batch of leads
  run_stage(stage)     — run one stage end-to-end (ingest → select → execute → decide)
  run_cycle()          — one pass over the forward pipeline + optimize + report
  run_until_drained()  — keep cycling until no lead is in an actionable state
  tick(now)            — run whatever the daily schedule says is due at `now`
  health() / report()  — monitoring + reporting snapshots

Coordination state lives in OrchestratorStore; the agents own the real work via
injected adapters (simulated by default, live via importlib when simulate=False).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from . import models as M
from . import state_machine as SM
from .adapters import build_adapters
from .adapters.base import StageContext
from .circuit_breaker import RetryPolicy
from .config import OrchestratorConfig
from .models import DailyReport, HealthSnapshot, PipelineLead, RunRecord, StageResult
from .responsibilities import Control, Decide, Monitor, Optimize, Report, Trigger
from .store import OrchestratorStore

logger = logging.getLogger(__name__)

# States a lead can still be actioned out of (used to detect a drained pipeline).
_ACTIONABLE = {
    M.DISCOVERED, M.RESEARCHED, M.READY, M.SENT, M.FOLLOWING_UP, M.REPLIED, M.ERROR,
}

# Forward-pipeline stages run each cycle (find is triggered separately so cycles
# don't endlessly inject new volume).
_CYCLE_STAGE_NAMES = ["reply", "followup", "send", "write", "research"]


class Orchestrator:
    def __init__(self, config: Optional[OrchestratorConfig] = None) -> None:
        self._config = config or OrchestratorConfig()
        self._store = OrchestratorStore(self._config.db_path)
        self._adapters = build_adapters(self._config)
        self._retry = RetryPolicy(self._config.retry)

        self._trigger = Trigger(self._config)
        self._control = Control(self._config, self._store)
        self._monitor = Monitor(self._config, self._store)
        self._decide = Decide(self._config)
        self._optimize = Optimize(self._config, self._store)
        self._report = Report(self._store)

    @property
    def store(self) -> OrchestratorStore:
        return self._store

    @property
    def config(self) -> OrchestratorConfig:
        return self._config

    # ── Stage execution ─────────────────────────────────────────────────────────

    async def run_find(self) -> StageResult:
        return await self.run_stage(SM.FIND)

    async def run_stage(self, stage: SM.Stage, now: Optional[datetime] = None) -> StageResult:
        now = now or datetime.now(timezone.utc)

        # MONITOR: circuit breaker gate (queue stages only).
        if stage.from_state is not None and not self._monitor.allow(stage.name, now):
            logger.info("Orchestrator: %s skipped — circuit open", stage.name)
            return StageResult(stage=stage.name, agent=stage.agent, ok=True)

        adapter = self._adapters[stage.name]

        # TRIGGER/CONTROL: pull this stage's input, then select a prioritized batch.
        await adapter.ingest(self._store, self._config, now)
        batch = self._control.select_batch(stage, now)
        if stage.from_state is not None and not batch:
            return StageResult(stage=stage.name, agent=stage.agent, ok=True)

        # EXECUTE.
        start = time.monotonic()
        ctx = StageContext(self._config, self._store, batch, now)
        try:
            result = await adapter.execute(ctx)
        except Exception as exc:           # transient stage failure
            logger.error("Orchestrator: %s failed — %s", stage.name, exc)
            result = StageResult(
                stage=stage.name, agent=stage.agent, ok=False,
                processed=len(batch), failed=len(batch), errors=[str(exc)],
            )
        result.duration_seconds = result.duration_seconds or (time.monotonic() - start)

        # MONITOR: feed the breaker + persist run history.
        if stage.from_state is not None:
            self._monitor.record(stage.name, result.ok, now)
        self._store.record_run(RunRecord(
            stage=stage.name, agent=stage.agent, processed=result.processed,
            succeeded=result.succeeded, failed=result.failed, ok=result.ok,
            error="; ".join(result.errors)[:500], duration_seconds=result.duration_seconds,
        ))

        # DECIDE: apply outcomes (or retry the batch on a transient failure).
        if not result.ok:
            for lid in batch:
                lead = self._store.get_lead(lid)
                if lead:
                    self._fail(lead, stage, "; ".join(result.errors) or "stage error", now)
        elif stage.from_state is not None:
            targets = self._decide.resolve(stage, result.outcomes)
            for lid, to_state in targets.items():
                lead = self._store.get_lead(lid)
                if lead:
                    self._transition(lead, to_state, f"{stage.name}:{result.outcomes.get(lid,'')}", now)

        # Tidy: exhausted sequences close out.
        self._auto_close_no_reply(now)
        return result

    # ── Cycles ──────────────────────────────────────────────────────────────────

    async def run_cycle(self, now: Optional[datetime] = None) -> dict[str, StageResult]:
        """One pass over the forward pipeline, then optimize + report."""
        now = now or datetime.now(timezone.utc)
        results: dict[str, StageResult] = {}
        for name in _CYCLE_STAGE_NAMES:
            results[name] = await self.run_stage(SM.STAGE_BY_NAME[name], now)
        self._optimize.tune()
        return results

    async def run_until_drained(self, max_cycles: int = 25) -> int:
        """Cycle until no lead is in an actionable state (or max_cycles hit)."""
        cycles = 0
        while cycles < max_cycles:
            cycles += 1
            await self.run_cycle()
            if not self._has_actionable_work():
                break
        logger.info("Orchestrator: drained after %d cycle(s)", cycles)
        return cycles

    async def tick(self, now: Optional[datetime] = None) -> dict[str, StageResult]:
        """Run whatever the daily schedule says is due at `now` (clock-driven)."""
        now = now or datetime.now()
        results: dict[str, StageResult] = {}
        for name in self._trigger.due_now(now):
            if name in SM.STAGE_BY_NAME:
                results[name] = await self.run_stage(SM.STAGE_BY_NAME[name], now)
            elif name == "health_check":
                self.health()
            elif name == "report":
                logger.info("\n%s", Report.render(self.report()))
        return results

    # ── Monitoring + reporting ──────────────────────────────────────────────────

    def health(self) -> HealthSnapshot:
        snap = self._monitor.snapshot()
        if not snap.healthy:
            logger.warning("Orchestrator health: %s", "; ".join(snap.alerts))
        return snap

    def report(self) -> DailyReport:
        notes = self._optimize.tune()
        return self._report.compile(self.health(), notes)

    # ── Manual controls ─────────────────────────────────────────────────────────

    def bump(self, lead_id: str) -> bool:
        """Human override: push a lead to the front of every queue."""
        lead = self._store.get_lead(lead_id)
        if not lead:
            return False
        lead.manual_bump = True
        self._store.upsert_lead(lead)
        return True

    # ── Transition helpers ──────────────────────────────────────────────────────

    def _transition(self, lead: PipelineLead, to_state: str, note: str, now: datetime) -> None:
        if lead.state == to_state:
            return
        if not SM.can_transition(lead.state, to_state):
            logger.warning(
                "Orchestrator: illegal transition %s → %s (lead %s); forcing",
                lead.state, to_state, lead.id,
            )
        prev = lead.state
        lead.state = to_state
        lead.state_entered_at = now
        lead.attempts = 0
        lead.last_error = ""
        lead.retry_after = None
        if to_state == M.SENT:
            lead.sent_at = now
        self._store.upsert_lead(lead)
        self._store.log_event(lead.id, prev, to_state, note)

    def _fail(self, lead: PipelineLead, stage: SM.Stage, error: str, now: datetime) -> None:
        lead.attempts += 1
        lead.last_error = error[:300]
        if self._retry.should_retry(lead.attempts):
            lead.retry_after = self._retry.next_retry_at(lead.attempts, now)
            self._store.upsert_lead(lead)
            logger.info(
                "Orchestrator: %s retry %d for lead %s after %s",
                stage.name, lead.attempts, lead.id, lead.retry_after,
            )
        else:
            prev = lead.state
            lead.state = M.DEAD
            lead.state_entered_at = now
            self._store.upsert_lead(lead)
            self._store.dead_letter(lead.id, stage.name, error[:300])
            self._store.log_event(lead.id, prev, M.DEAD, f"dead-letter: {error[:120]}")
            logger.warning("Orchestrator: lead %s dead-lettered at %s", lead.id, stage.name)

    def _auto_close_no_reply(self, now: datetime) -> None:
        for lead in self._store.leads_in_state(M.NO_REPLY):
            self._transition(lead, M.CLOSED, "sequence exhausted", now)

    def _has_actionable_work(self) -> bool:
        counts = self._store.count_by_state()
        return any(counts.get(s, 0) for s in _ACTIONABLE)
