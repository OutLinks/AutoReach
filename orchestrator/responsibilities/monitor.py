"""
Responsibility 3 — MONITOR: watches for errors and bottlenecks.

Owns one circuit breaker per stage and turns the store's coordination state into
a HealthSnapshot:

  - queue depth per stage (leads waiting in its input state),
  - in-progress / stuck leads (sitting in an ACTIVE state past the timeout),
  - recent error rate (failed / processed across recent runs),
  - bottlenecks (a queue with leads waiting longer than the max wait),
  - dead-letter backlog.

The engine consults `allow()` before running a stage and calls `record()` after,
so repeated failures trip the breaker and the stage is skipped until it cools off.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..circuit_breaker import CircuitBreaker
from ..config import OrchestratorConfig
from ..models import ACTIVE_STATES, HealthSnapshot, StageHealth
from ..state_machine import FOLLOWUP, QUEUE_STAGES, Stage
from ..store import OrchestratorStore

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, config: OrchestratorConfig, store: OrchestratorStore) -> None:
        self._config = config
        self._store = store
        self._breakers: dict[str, CircuitBreaker] = {
            stage.name: CircuitBreaker(
                stage.name,
                config.retry.circuit_failure_threshold,
                config.retry.circuit_cooldown_seconds,
            )
            for stage in QUEUE_STAGES
        }

    # ── Circuit breaker gate ────────────────────────────────────────────────────

    def allow(self, stage_name: str, now: datetime | None = None) -> bool:
        breaker = self._breakers.get(stage_name)
        return breaker.allow(now) if breaker else True

    def record(self, stage_name: str, ok: bool, now: datetime | None = None) -> None:
        breaker = self._breakers.get(stage_name)
        if not breaker:
            return
        if ok:
            breaker.record_success()
        else:
            breaker.record_failure(now)

    def circuit_state(self, stage_name: str) -> str:
        breaker = self._breakers.get(stage_name)
        return breaker.state if breaker else "closed"

    # ── Health snapshot ─────────────────────────────────────────────────────────

    def snapshot(self, now: datetime | None = None) -> HealthSnapshot:
        now = now or datetime.now(timezone.utc)
        snap = HealthSnapshot(dead_letter_count=self._store.dead_letter_count())

        for stage in QUEUE_STAGES:
            waiting = self._waiting_leads(stage)
            oldest = self._oldest_wait_hours(waiting, now)
            health = StageHealth(
                stage=stage.name,
                queue_depth=len(waiting),
                in_progress=self._in_progress_count(stage),
                error_rate=self._error_rate(stage),
                circuit=self.circuit_state(stage.name),
                oldest_wait_hours=oldest,
                bottleneck=oldest > self._config.retry.max_queue_wait_hours,
            )
            snap.stages.append(health)

            if health.circuit == "open":
                snap.alerts.append(f"{stage.name}: circuit OPEN")
            if health.bottleneck:
                snap.alerts.append(
                    f"{stage.name}: bottleneck — lead waiting {oldest:.1f}h"
                )
            if health.error_rate > 0.05 and health.queue_depth >= 0:
                snap.alerts.append(
                    f"{stage.name}: error rate {health.error_rate:.0%} > 5%"
                )

        if snap.dead_letter_count:
            snap.alerts.append(f"dead letter: {snap.dead_letter_count} lead(s) need review")

        snap.healthy = not snap.alerts
        return snap

    # ── Internals ───────────────────────────────────────────────────────────────

    def _error_rate(self, stage: Stage) -> float:
        runs = self._store.recent_runs(stage.name, limit=5)
        processed = sum(r["processed"] for r in runs)
        failed = sum(r["failed"] for r in runs)
        return round(failed / processed, 4) if processed else 0.0

    def _waiting_leads(self, stage: Stage):
        waiting = self._store.leads_in_state(stage.from_state) if stage.from_state else []
        if stage.name != FOLLOWUP.name:
            return waiting
        eligible = []
        for lead in waiting:
            campaign = self._store.get_campaign(lead.source_job) if lead.source_job else None
            if campaign and not campaign.send_policy.followup_days:
                continue
            eligible.append(lead)
        return eligible

    def _in_progress_count(self, stage: Stage) -> int:
        if stage.in_progress_state not in ACTIVE_STATES:
            return 0
        return len(self._store.leads_in_state(stage.in_progress_state))

    @staticmethod
    def _oldest_wait_hours(leads, now: datetime) -> float:
        oldest = 0.0
        for lead in leads:
            entered = lead.state_entered_at
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours = (now - entered).total_seconds() / 3600
            oldest = max(oldest, hours)
        return round(oldest, 2)
