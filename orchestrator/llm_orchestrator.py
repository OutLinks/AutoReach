"""LLM intent routing with deterministic orchestration tools.

The model chooses which capability is appropriate for a user request, but it
never owns lifecycle state. Every tool is backed by the normal orchestrator or
interactive workflow and therefore keeps the state machine, quality gates,
send safeguards, and audit trail in charge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.model_selection import Message, ModelConfig, ToolDefinition, get_model


ORCHESTRATOR_SYSTEM_PROMPT = """\
You are AutoReach's operations orchestrator. You translate an operator's
request into safe, concrete tool calls.

Operating rules:
1. Choose the smallest workflow that satisfies the request. Use standalone
   research or email-drafting tools when the user asks for only that task; use
   pipeline tools only when the user asks to run or continue a campaign.
2. Inspect lead state or available actions before advancing an existing lead.
   The backend state machine is authoritative. Never invent a state transition,
   skip a required stage, or claim that an action succeeded without a tool
   result.
3. Never send an email unless the user explicitly requests sending and the
   backend confirms that the draft is approved. Drafting is not sending.
4. Treat user-provided facts as the evidence boundary. Do not invent company
   facts, contacts, sources, delivery results, or meetings.
5. If required identifiers or context are missing, ask for them instead of
   running a broad pipeline as a guess.
6. Use one tool at a time when an operation depends on a previous result. After
   tools finish, give a concise factual summary and mention any next action
   that still requires user approval.

Available tools expose the state machine as safe operations. You may request
available actions, but only the backend can validate and execute a transition.
"""


ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class OrchestrationResult:
    """Normalized result of one LLM planning/tool-execution conversation."""

    action: str = "respond"
    response: str = ""
    result: Any = None
    used_llm: bool = True
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def orchestration_tools() -> list[ToolDefinition]:
    """Return the intentionally narrow tool surface exposed to the model."""

    return [
        ToolDefinition(
            name="get_pipeline_state",
            description="Inspect a lead or the current pipeline funnel and campaign.",
            parameters={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="get_available_actions",
            description="List the legal next actions for one lead according to the state machine.",
            parameters={
                "type": "object",
                "properties": {"lead_id": {"type": "string"}},
                "required": ["lead_id"],
            },
        ),
        ToolDefinition(
            name="research_company",
            description="Run standalone research for a company or existing lead.",
            parameters={
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "lead_id": {"type": "string"},
                    "prompt": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="draft_email",
            description="Generate an email draft for an existing lead and campaign. This never sends it.",
            parameters={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "campaign_id": {"type": "string"},
                    "tone": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["lead_id", "campaign_id"],
            },
        ),
        ToolDefinition(
            name="run_pipeline_stage",
            description="Run one legal named pipeline stage for currently eligible leads.",
            parameters={
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "enum": ["find", "research", "write", "send", "followup", "reply"],
                    },
                },
                "required": ["stage"],
            },
        ),
        ToolDefinition(
            name="run_pipeline_cycle",
            description="Run one complete orchestrator cycle over the existing campaign pipeline.",
            parameters={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="find_leads",
            description="Start the lead-finding stage for the active campaign.",
            parameters={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="send_approved_email",
            description="Send an already approved email draft after backend safeguards pass.",
            parameters={
                "type": "object",
                "properties": {"draft_id": {"type": "string"}},
                "required": ["draft_id"],
            },
        ),
        ToolDefinition(
            name="get_pipeline_health",
            description="Return current queue, circuit-breaker, and dead-letter health.",
            parameters={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="get_pipeline_report",
            description="Return the current funnel, conversion, alerts, and optimization report.",
            parameters={"type": "object", "properties": {}},
        ),
    ]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class LLMOrchestrator:
    """Run a bounded provider-neutral tool-calling loop."""

    def __init__(
        self,
        config: ModelConfig,
        adapter: Any | None = None,
        max_turns: int = 6,
    ) -> None:
        self.config = config
        self.adapter = adapter or get_model(config)
        self.max_turns = max(1, max_turns)

    async def run(
        self,
        message: str,
        context: dict[str, Any] | None,
        execute_tool: ToolExecutor,
    ) -> OrchestrationResult:
        context = context or {}
        user_content = json.dumps(
            {"request": message, "context": context},
            default=str,
        )
        messages = [
            Message(role="system", content=ORCHESTRATOR_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]
        called: list[dict[str, Any]] = []
        last_result: Any = None
        last_action = "respond"

        for _ in range(self.max_turns):
            response = await self.adapter.complete(
                self.config,
                messages,
                orchestration_tools(),
            )
            if not response.tool_calls:
                return OrchestrationResult(
                    action=last_action,
                    response=response.content or "",
                    result=last_result,
                    tool_calls=called,
                )

            messages.append(Message(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))
            for tool_call in response.tool_calls:
                tool_content: dict[str, Any]
                last_action = tool_call.name
                called.append({"name": tool_call.name, "arguments": {}})
                try:
                    args = tool_call.parsed_arguments()
                    called[-1]["arguments"] = args
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    args = {}
                    tool_content = {
                        "ok": False,
                        "error": f"Invalid tool arguments: {exc}",
                    }
                    last_result = tool_content
                else:
                    try:
                        last_result = await execute_tool(tool_call.name, args)
                        tool_content = {"ok": True, "result": _jsonable(last_result)}
                    except Exception as exc:
                        # Give the model a structured rejection so it can ask
                        # for missing context or choose a safer alternative.
                        tool_content = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        last_result = tool_content
                messages.append(Message(
                    role="tool",
                    content=json.dumps(tool_content, default=str),
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                ))

        return OrchestrationResult(
            action=last_action,
            response="The orchestration tool loop reached its safety limit.",
            result=last_result,
            tool_calls=called,
        )
