"""
Responsibility 2 — CONTROL: decides WHAT each agent works on.

Owns the priority queue and concurrency/volume caps. For a given stage it:
  1. pulls every lead waiting in that stage's input state,
  2. drops leads whose retry timer hasn't elapsed,
  3. (re)computes each lead's priority score,
  4. returns the top-N where N respects the stage's concurrency cap and the
     relevant daily volume budget.

Priority (architecture doc):
    score = quality*0.4 + size*0.2 + fit*0.2 + recency*0.2
A manual bump forces a lead to the front regardless of score.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import OrchestratorConfig
from ..models import FOLLOWING_UP, PipelineLead, SENT
from ..state_machine import FIND, FOLLOWUP, SEND, Stage
from ..store import OrchestratorStore

logger = logging.getLogger(__name__)


class Control:
    def __init__(self, config: OrchestratorConfig, store: OrchestratorStore) -> None:
        self._config = config
        self._store = store

    # ── Priority ───────────────────────────────────────────────────────────────

    def priority_score(self, lead: PipelineLead) -> float:
        return round(
            lead.quality_score * 0.4
            + lead.company_size_score * 0.2
            + lead.industry_fit_score * 0.2
            + lead.recency_score * 0.2,
            4,
        )

    # ── Batch selection ────────────────────────────────────────────────────────

    def select_batch(self, stage: Stage, now: datetime | None = None) -> list[str]:
        """Return the prioritized lead ids this stage should process now."""
        if stage.name == FIND.name or stage.from_state is None:
            return []  # find creates leads; nothing to select

        now = now or datetime.now(timezone.utc)
        # The follow-up stage services leads still in cadence (SENT or FOLLOWING_UP).
        source_states = (
            [SENT, FOLLOWING_UP] if stage.name == FOLLOWUP.name else [stage.from_state]
        )
        waiting = [
            lead
            for state in source_states
            for lead in self._store.leads_in_state(state)
            if self._ready(lead, now)
        ]

        for lead in waiting:
            lead.recency_score = self._recency(lead, now)
            lead.priority = self.priority_score(lead)

        waiting.sort(key=lambda l: (l.manual_bump, l.priority), reverse=True)

        cap = self._batch_cap(stage)
        batch = waiting[:cap]
        logger.info(
            "Control[%s]: %d waiting, selected %d (cap %d)",
            stage.name, len(waiting), len(batch), cap,
        )
        return [l.id for l in batch]

    def _batch_cap(self, stage: Stage) -> int:
        cap = self._config.concurrency.get(stage.name, 5)
        if stage.name == SEND.name:
            cap = min(cap, self._config.volume.emails_per_day)
        elif stage.name == FOLLOWUP.name:
            cap = min(cap, self._config.volume.followups_per_day)
        return max(cap, 0)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _ready(lead: PipelineLead, now: datetime) -> bool:
        if lead.retry_after is None:
            return True
        ra = lead.retry_after
        if ra.tzinfo is None:
            ra = ra.replace(tzinfo=timezone.utc)
        return ra <= now

    @staticmethod
    def _recency(lead: PipelineLead, now: datetime) -> float:
        """Fresher leads score higher; decays to ~0 over 14 days."""
        entered = lead.created_at
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - entered).total_seconds() / 86400)
        return round(max(0.0, 1.0 - age_days / 14.0), 4)
