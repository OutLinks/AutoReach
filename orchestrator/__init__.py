"""
AutoReach Orchestrator — the brain that coordinates Agents 1–5.

    from orchestrator import Orchestrator, OrchestratorConfig

    orch = Orchestrator(OrchestratorConfig())   # simulate=True by default
    await orch.run_find()                        # Agent 1 → new leads
    await orch.run_until_drained()               # push them through the pipeline
    print(orch.report())
"""

from .orchestrator import Orchestrator
from .config import OrchestratorConfig
from .models import PipelineLead, DailyReport, HealthSnapshot

__all__ = [
    "Orchestrator",
    "OrchestratorConfig",
    "PipelineLead",
    "DailyReport",
    "HealthSnapshot",
]
