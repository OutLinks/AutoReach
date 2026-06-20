"""
Adapter factory.

build_adapters() returns one adapter per stage, choosing simulated or live based
on config.simulate. The Orchestrator depends only on the AgentAdapter interface,
so swapping the whole agent fleet for deterministic stand-ins is a one-flag change.
"""

from __future__ import annotations

from ..config import OrchestratorConfig
from ..state_machine import STAGES, Stage
from .base import AgentAdapter, StageContext
from .simulated import SimulatedAdapter
from .live import LiveAdapter

__all__ = ["AgentAdapter", "StageContext", "SimulatedAdapter", "LiveAdapter", "build_adapters"]


def build_adapters(config: OrchestratorConfig) -> dict[str, AgentAdapter]:
    factory = SimulatedAdapter if config.simulate else LiveAdapter
    return {stage.name: factory(stage) for stage in STAGES}
