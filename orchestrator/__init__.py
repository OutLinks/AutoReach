"""
AutoReach Orchestrator — the brain that coordinates Agents 1–5.

    from orchestrator import Orchestrator, OrchestratorConfig

    orch = Orchestrator(OrchestratorConfig())   # simulate=True by default
    brief = await orch.create_campaign("Find B2B SaaS founders from https://...")
    orch.activate_campaign(brief.id)            # explicit review/activation step
    await orch.run_find()                        # Agent 1 → new leads
    await orch.run_until_drained()               # push them through the pipeline
    print(orch.report())
"""

from .orchestrator import Orchestrator
from .config import OrchestratorConfig
from .models import PipelineLead, DailyReport, HealthSnapshot
from .campaigns import CampaignBrief, CampaignPlanner

__all__ = [
    "Orchestrator",
    "OrchestratorConfig",
    "PipelineLead",
    "DailyReport",
    "HealthSnapshot",
    "CampaignBrief",
    "CampaignPlanner",
]
