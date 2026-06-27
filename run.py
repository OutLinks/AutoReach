#!/usr/bin/env python3
"""Unified container entrypoint for the autoreach pipeline.

One image, one command surface. Pick what a container should run via argv:

    python run.py orchestrator        # the CEO: drives all 5 agents in-process
    python run.py agent1 "<prompt>"   # Lead Finder       (standalone)
    python run.py agent2              # Research Analyst  (standalone)
    python run.py agent3             # Email Writer      (standalone)
    python run.py agent4            # Sender            (standalone)
    python run.py agent5           # Reply Handler     (standalone)

The orchestrator is the normal way to run the system end-to-end — it owns the
state machine and routes leads between stages. The per-agent commands exist so
you can exercise / debug a single stage in isolation. Both paths read and write
the same JSONL hand-off files under agents/*/output, so when the agent services
share those volumes with the orchestrator they all see the same pipeline state.

Behaviour is tuned through env vars (see docker-compose.yml / .env):
    ORCH_SIMULATE   "1" (default) uses simulated adapters; "0" runs real agents.
    ORCH_MODE       "loop" (default) keeps cycling; "once" runs a single pass.
    ORCH_INTERVAL   seconds to sleep between cycles in loop mode (default 300).
    AGENT_LEAD_IDS  comma-separated lead ids for agent2/3/4 standalone runs.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("run")

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = ROOT / "agents"


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def _lead_ids() -> list[str] | None:
    raw = os.getenv("AGENT_LEAD_IDS", "").strip()
    return [x for x in (s.strip() for s in raw.split(",")) if x] or None


def _load_agent(dirname: str, alias: str):
    """Import a hyphenated agent package under an importable alias.

    Mirrors orchestrator/adapters/live.py so standalone runs and orchestrated
    runs construct agents identically.
    """
    pkg = AGENTS_DIR / dirname
    (pkg / "output").mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        alias, pkg / "__init__.py", submodule_search_locations=[str(pkg)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load agent package {dirname}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


# ── orchestrator ────────────────────────────────────────────────────────────


async def _run_orchestrator() -> None:
    from orchestrator.config import OrchestratorConfig
    from orchestrator.orchestrator import Orchestrator
    from orchestrator.responsibilities import Report

    simulate = _env_bool("ORCH_SIMULATE", True)
    mode = os.getenv("ORCH_MODE", "loop").strip().lower()
    interval = int(os.getenv("ORCH_INTERVAL", "300"))

    Path(OrchestratorConfig().db_path).parent.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(OrchestratorConfig(simulate=simulate))
    log.info("Orchestrator starting (simulate=%s, mode=%s, interval=%ss)", simulate, mode, interval)

    async def one_pass() -> None:
        await orch.run_find()
        cycles = await orch.run_until_drained()
        log.info("Pipeline drained after %d cycle(s)", cycles)
        log.info("\n%s", Report.render(orch.report()))

    await one_pass()
    if mode == "once":
        return
    while True:
        log.info("Sleeping %ss before next cycle…", interval)
        await asyncio.sleep(interval)
        await one_pass()


# ── individual agents (standalone / debug) ──────────────────────────────────


async def _run_agent1(args: list[str]) -> None:
    mod = _load_agent("agent1-lead-finder", "agent1_lead_finder")
    prompt = args[0] if args else os.getenv("AGENT1_PROMPT", "Find leads")
    agent = mod.LeadFinderAgent(mod.ServiceConfig())
    await agent.run(prompt)


async def _run_agent2(_args: list[str]) -> None:
    mod = _load_agent("agent2-research-analyst", "agent2_research_analyst")
    agent = mod.ResearchAgent(mod.ServiceConfig())
    await agent.run(lead_ids=_lead_ids())


async def _run_agent3(_args: list[str]) -> None:
    mod = _load_agent("agent3-email-writer", "agent3_email_writer")
    agent = mod.EmailWriterAgent(mod.ServiceConfig())
    await agent.run(lead_ids=_lead_ids())


async def _run_agent4(_args: list[str]) -> None:
    mod = _load_agent("agent4-sender", "agent4_sender")
    agent = mod.SenderAgent(mod.ServiceConfig.from_env())
    await agent.run_initial(lead_ids=_lead_ids())


async def _run_agent5(_args: list[str]) -> None:
    mod = _load_agent("agent5-reply-handler", "agent5_reply_handler")
    agent = mod.ReplyHandlerAgent(mod.ServiceConfig.from_env())
    await agent.run()


_TARGETS = {
    "orchestrator": lambda args: _run_orchestrator(),
    "agent1": _run_agent1,
    "agent2": _run_agent2,
    "agent3": _run_agent3,
    "agent4": _run_agent4,
    "agent5": _run_agent5,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in _TARGETS:
        print(__doc__)
        return 2
    target, args = sys.argv[1], sys.argv[2:]
    log.info("Launching target: %s", target)
    try:
        asyncio.run(_TARGETS[target](args))
        return 0
    except KeyboardInterrupt:
        log.info("Interrupted, shutting down")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
