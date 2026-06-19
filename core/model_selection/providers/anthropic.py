"""
Anthropic (Claude) provider adapter.

Wire format differences vs OpenAI:
  - `system` prompt is a top-level param, not a message
  - Tools use `input_schema` instead of `parameters`
  - Tool results are nested content blocks (type="tool_result") inside a user message
  - Stop reasons: "end_turn" → "stop", "tool_use" → "tool_calls", "max_tokens" → "length"
  - Usage: `input_tokens` / `output_tokens` (not prompt_tokens / completion_tokens)
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..base import ProviderAdapter
from ..registry import register_provider
from ..types import Message, ModelConfig, ModelResponse, ToolCall, ToolDefinition, Usage

_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


class AnthropicAdapter(ProviderAdapter):
    """Adapter for Anthropic's Messages API (claude-* models)."""

    def __init__(self) -> None:
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic as _anthropic
            except ImportError:
                raise RuntimeError(
                    "anthropic package is not installed. Run: pip install anthropic"
                )
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY environment variable is not set"
                )
            self._client = _anthropic.AsyncAnthropic(api_key=api_key)
        return self._client

    def build_request(
        self,
        config: ModelConfig,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> dict[str, Any]:
        system_parts: list[str] = []
        api_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                # Anthropic takes system as a top-level param — collect all
                # system messages and join them
                text = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
                system_parts.append(text)

            elif msg.role == "tool":
                # Tool results must be wrapped in a user message as tool_result blocks
                content_text = (
                    msg.content
                    if isinstance(msg.content, str)
                    else json.dumps(msg.content)
                )
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": content_text,
                        }
                    ],
                })

            else:
                api_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": api_messages,
        }

        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        if config.temperature is not None:
            kwargs["temperature"] = config.temperature

        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,   # Anthropic's name for JSON Schema
                }
                for t in tools
            ]

        # Merge provider-specific overrides last so they can override anything above
        kwargs.update(config.extra)

        return kwargs

    def normalize_response(self, raw: Any) -> ModelResponse:
        content: str | None = None
        tool_calls: list[ToolCall] | None = None

        for block in raw.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=json.dumps(block.input),
                    )
                )

        finish_reason = _STOP_REASON_MAP.get(raw.stop_reason or "end_turn", "stop")

        usage: Usage | None = None
        if raw.usage:
            u = raw.usage
            inp = getattr(u, "input_tokens", 0) or 0
            out = getattr(u, "output_tokens", 0) or 0
            cached = getattr(u, "cache_read_input_tokens", 0) or 0
            usage = Usage(
                prompt_tokens=inp,
                completion_tokens=out,
                total_tokens=inp + out,
                cached_tokens=cached,
            )

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def complete(
        self,
        config: ModelConfig,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> ModelResponse:
        client = self._get_client()
        kwargs = self.build_request(config, messages, tools)
        raw = await client.messages.create(**kwargs)
        return self.normalize_response(raw)


register_provider("anthropic", AnthropicAdapter)
