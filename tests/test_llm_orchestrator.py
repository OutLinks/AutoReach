from __future__ import annotations

import asyncio
import json
import unittest

from core.model_selection import Message, ModelConfig, ModelResponse, ToolCall
from core.model_selection.providers.anthropic import AnthropicAdapter
from core.model_selection.providers.openai import OpenAIAdapter
from orchestrator.llm_orchestrator import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    LLMOrchestrator,
)
from orchestrator.models import DISCOVERED, REPLIED, RESEARCHED
from orchestrator.state_machine import available_stages


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.step = 0

    async def complete(self, config, messages, tools):
        self.calls.append(list(messages))
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content=None,
                tool_calls=[ToolCall(
                    id="call-1",
                    name="get_available_actions",
                    arguments=json.dumps({"lead_id": "lead-1"}),
                )],
                finish_reason="tool_calls",
            )
        return ModelResponse(
            content="The lead is ready for research.",
            tool_calls=None,
            finish_reason="stop",
        )


class LLMOrchestratorTests(unittest.TestCase):
    def test_state_machine_exposes_only_legal_stage_tools(self) -> None:
        self.assertEqual(available_stages(DISCOVERED), ["research"])
        self.assertEqual(available_stages(RESEARCHED), ["write"])
        self.assertEqual(available_stages(REPLIED, reply_handling_enabled=False), [])
        self.assertEqual(available_stages(REPLIED, reply_handling_enabled=True), ["reply"])

    def test_tool_loop_replays_assistant_call_and_tool_result(self) -> None:
        adapter = _FakeAdapter()
        seen: list[tuple[str, dict]] = []

        async def execute(name: str, args: dict):
            seen.append((name, args))
            return {"state": "discovered", "actions": ["research"]}

        result = asyncio.run(
            LLMOrchestrator(
                ModelConfig(provider="test", model="test"),
                adapter=adapter,
            ).run("What can I do with this lead?", {}, execute)
        )

        self.assertEqual(result.action, "get_available_actions")
        self.assertEqual(result.response, "The lead is ready for research.")
        self.assertEqual(seen, [("get_available_actions", {"lead_id": "lead-1"})])
        self.assertEqual(adapter.calls[1][2].role, "assistant")
        self.assertEqual(adapter.calls[1][2].tool_calls[0].name, "get_available_actions")
        self.assertEqual(adapter.calls[1][3].role, "tool")
        self.assertIn("state machine", ORCHESTRATOR_SYSTEM_PROMPT)

    def test_provider_requests_can_replay_assistant_tool_calls(self) -> None:
        call = ToolCall(id="call-1", name="get_pipeline_state", arguments="{}")
        messages = [
            Message(role="assistant", content="", tool_calls=[call]),
            Message(
                role="tool",
                content='{"ok": true}',
                tool_call_id="call-1",
                name="get_pipeline_state",
            ),
        ]
        config = ModelConfig(provider="test", model="test")

        openai_request = OpenAIAdapter().build_request(config, messages)
        self.assertEqual(openai_request["messages"][0]["tool_calls"][0]["function"]["name"], "get_pipeline_state")
        self.assertEqual(openai_request["messages"][1]["role"], "tool")

        anthropic_request = AnthropicAdapter().build_request(config, messages)
        self.assertEqual(anthropic_request["messages"][0]["content"][0]["type"], "tool_use")
        self.assertEqual(anthropic_request["messages"][1]["content"][0]["type"], "tool_result")


if __name__ == "__main__":
    unittest.main()
