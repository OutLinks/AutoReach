"""
Agent adapter interface.

The Orchestrator never imports an agent directly — it drives them through a
uniform adapter so the coordination logic is identical whether an agent runs for
real, is simulated, or is swapped out. Each adapter wraps exactly one stage.

An adapter does two things:
  ingest()  — pull this stage's input into the master store (find creates leads;
              reply flips SENT→REPLIED from Agent 4's reply hand-offs). Default no-op.
  execute() — run the agent over the batch the control layer selected and return a
              normalized StageResult (what advanced, new leads, per-lead outcomes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import OrchestratorConfig
from ..campaigns import CampaignBrief
from ..models import StageResult
from ..state_machine import Stage
from ..store import OrchestratorStore


@dataclass
class StageContext:
    config: OrchestratorConfig
    store: OrchestratorStore
    lead_ids: list[str]
    now: datetime
    campaign: CampaignBrief | None = None


class AgentAdapter(ABC):
    def __init__(self, stage: Stage) -> None:
        self.stage = stage

    async def ingest(
        self,
        store: OrchestratorStore,
        config: OrchestratorConfig,
        now: datetime | None = None,
    ) -> int:
        """Pull external input for this stage into the store. Returns count added."""
        return 0

    @abstractmethod
    async def execute(self, ctx: StageContext) -> StageResult:
        ...

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
