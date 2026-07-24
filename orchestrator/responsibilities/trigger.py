"""
Responsibility 1 — TRIGGER: decides WHEN each stage runs.

Two trigger modes:
  - scheduled: the daily plan (ScheduleConfig) maps an hour to the stages due then,
  - continuous: in a run_cycle, every stage that has work queued is eligible.

The trigger doesn't run anything — it answers "what is due right now?" The engine
combines that with the monitor (circuit breakers) and control (queues) to act.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..config import OrchestratorConfig
from ..state_machine import QUEUE_STAGES, STAGE_BY_NAME, Stage

logger = logging.getLogger(__name__)


class Trigger:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._config = config

    def due_now(self, now: datetime | None = None) -> list[str]:
        """Stage names scheduled for the current hour (plus pseudo-stages)."""
        now = now or datetime.now()
        return list(self._config.schedule.plan.get(now.hour, []))

    def is_due(self, stage_name: str, now: datetime | None = None) -> bool:
        return stage_name in self.due_now(now)

    def cycle_order(self) -> list[Stage]:
        """
        The order to attempt stages in a full pipeline cycle. When enabled,
        replies and follow-ups run before new volume is added; then the forward
        pipeline runs find → research → write → send.
        """
        names = ["followup", "send", "write", "research", "find"]
        if self._config.reply_handling_enabled:
            names.insert(0, "reply")
        return [STAGE_BY_NAME[n] for n in names]

    @staticmethod
    def queue_stages() -> list[Stage]:
        return list(QUEUE_STAGES)
