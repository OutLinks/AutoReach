"""
LLM-driven orchestrator.

Instead of running stages on a fixed schedule, this hands control to an LLM:
each step the model sees the current pipeline state and the log of actions taken
so far, then calls a tool to decide what runs next (find leads, research, write,
send, follow up, handle replies) — or `finish` when no productive work remains.

The underlying `Orchestrator` still owns all real work (agents, state machine,
circuit breakers); this layer only chooses *what to call and when*, entirely via
LLM tool calls.

Design note: the loop re-plans from scratch each step (fresh message history with
the live state injected) rather than threading a stateful tool conversation. That
keeps it robust with any OpenAI-/Anthropic-compatible adapter and lets the model
always reason over ground-truth state instead of a possibly-stale transcript.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from core.model_selection import Message, ModelConfig, ToolDefinition, get_model

from . import state_machine as SM
from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# ── Tools the controller LLM may call ─────────────────────────────────────────

_ACTION_STAGES = ["research", "write", "send", "followup", "reply"]

_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="run_find",
        description=(
            "Trigger Agent 1 to discover a fresh batch of leads. Use only when "
            "the pipeline is starved of new leads — it injects new volume."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    ToolDefinition(
        name="run_stage",
        description=(
            "Run one forward-pipeline stage end-to-end over its queued leads. "
            "research: DISCOVERED→RESEARCHED. write: RESEARCHED→READY. "
            "send: READY→SENT. followup: SENT→FOLLOWING_UP. reply: REPLIED→HANDLED."
        ),
        parameters={
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": _ACTION_STAGES,
                    "description": "Which stage to run.",
                }
            },
            "required": ["stage"],
        },
    ),
    ToolDefinition(
        name="finish",
        description="Stop orchestrating. Call when no productive work remains.",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why you are stopping."}
            },
            "required": ["reason"],
        },
    ),
]

_SYSTEM_PROMPT = """\
You are the autonomous Orchestrator of a 5-agent cold-outreach system. You are the
decision-maker: you choose which agent/stage runs next, in what order, based on the
live pipeline state. You act ONLY through the provided tools.

The lead lifecycle (state → stage that advances it):
  DISCOVERED  --run_stage(research)-->  RESEARCHED
  RESEARCHED  --run_stage(write)----->  READY
  READY       --run_stage(send)------>  SENT
  SENT        --run_stage(followup)-->  FOLLOWING_UP
  REPLIED     --run_stage(reply)----->  HANDLED / MEETING_BOOKED
  (run_find injects brand-new DISCOVERED leads)

Strategy:
- Prioritise moving existing leads forward (replies first — they are warmest —
  then sends, follow-ups, writing, research) before discovering new volume.
- Only call run_find when there is little or no actionable work left upstream.
- A stage with an empty queue is a no-op; don't repeatedly run empty stages.
- When every actionable queue is empty and no further progress is possible,
  call finish with a short summary.

Each turn you receive the current state counts and the log of actions you've
already taken. Call exactly one tool for the single best next action."""


class LLMOrchestrator:
    """Drives the deterministic Orchestrator via LLM tool calls."""

    def __init__(
        self,
        orchestrator: Optional[Orchestrator] = None,
        model: Optional[ModelConfig] = None,
    ) -> None:
        self._orch = orchestrator or Orchestrator()
        self._model = model or ModelConfig(
            provider=os.environ.get("LLM_PROVIDER", "anthropic"),
            model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
            max_tokens=1024,
            temperature=0.0,
        )
        self._adapter = get_model(self._model)

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orch

    async def run(self, goal: str, max_steps: int = 25) -> list[dict[str, Any]]:
        """Let the LLM orchestrate until it calls finish or max_steps is hit.

        Returns the action log: one dict per executed tool call.
        """
        action_log: list[dict[str, Any]] = []

        for step in range(1, max_steps + 1):
            messages = [
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=self._render_context(goal, action_log)),
            ]
            response = await self._adapter.complete(self._model, messages, _TOOLS)

            if not response.tool_calls:
                logger.info("LLMOrchestrator: model returned no tool call — stopping")
                action_log.append({"step": step, "tool": None,
                                   "note": (response.content or "")[:200]})
                break

            call = response.tool_calls[0]      # one decisive action per turn
            try:
                args = call.parsed_arguments()
            except Exception:
                args = {}

            logger.info("LLMOrchestrator step %d → %s(%s)", step, call.name, args)

            if call.name == "finish":
                action_log.append({"step": step, "tool": "finish",
                                   "reason": args.get("reason", "")})
                break

            result = await self._execute(call.name, args)
            action_log.append({"step": step, "tool": call.name,
                               "args": args, "result": result})

        return action_log

    # ── Tool execution ────────────────────────────────────────────────────────

    async def _execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "run_find":
            res = await self._orch.run_find()
            return {"new_leads": len(res.new_lead_ids), "ok": res.ok}

        if name == "run_stage":
            stage_name = args.get("stage", "")
            stage = SM.STAGE_BY_NAME.get(stage_name)
            if stage is None or stage_name not in _ACTION_STAGES:
                return {"error": f"unknown stage {stage_name!r}"}
            res = await self._orch.run_stage(stage)
            return {
                "stage": stage_name,
                "processed": res.processed,
                "succeeded": res.succeeded,
                "failed": res.failed,
                "ok": res.ok,
            }

        return {"error": f"unknown tool {name!r}"}

    # ── Context rendering ─────────────────────────────────────────────────────

    def _render_context(self, goal: str, action_log: list[dict[str, Any]]) -> str:
        counts = self._orch.store.count_by_state()
        counts_str = (
            "\n".join(f"  {state}: {n}" for state, n in sorted(counts.items()) if n)
            or "  (no leads)"
        )
        if action_log:
            log_str = "\n".join(
                f"  {a['step']}. {a['tool']}({a.get('args', {})}) -> {a.get('result', '')}"
                for a in action_log
            )
        else:
            log_str = "  (none yet)"

        return (
            f"GOAL: {goal}\n\n"
            f"CURRENT PIPELINE STATE (lead counts by state):\n{counts_str}\n\n"
            f"ACTIONS TAKEN SO FAR:\n{log_str}\n\n"
            f"Call one tool for the next best action."
        )
