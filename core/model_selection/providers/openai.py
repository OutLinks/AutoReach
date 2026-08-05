"""
OpenAI provider adapter — and base class for all OpenAI-compatible providers.

`ChatCompletionsAdapter` is a reusable base. Subclass it to point at any
OpenAI-compatible endpoint (OpenRouter, local Ollama, etc.) by setting
`BASE_URL` and `API_KEY_ENV`.

Wire format:
  - System prompt is a message with role="system" (included in messages list)
  - Tools use `{"type": "function", "function": {"name", "description", "parameters"}}`
  - Tool results use role="tool" with `tool_call_id`
  - Response: choices[0].message.content / tool_calls
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..base import ProviderAdapter
from ..registry import register_provider
from ..types import Message, ModelConfig, ModelResponse, ToolCall, ToolDefinition, Usage

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "length": "length",
    "content_filter": "content_filter",
}


class ChatCompletionsAdapter(ProviderAdapter):
    """
    Base adapter for any OpenAI-compatible chat completions endpoint.

    Subclass and set `BASE_URL` and `API_KEY_ENV` to target a different provider:

        class MyAdapter(ChatCompletionsAdapter):
            BASE_URL = "https://my-provider.com/v1"
            API_KEY_ENV = "MY_PROVIDER_API_KEY"
    """

    BASE_URL: str = ""          # Empty = use OpenAI's default endpoint
    API_KEY_ENV: str = "OPENAI_API_KEY"

    def __init__(self) -> None:
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai as _openai
            except ImportError:
                raise RuntimeError(
                    "openai package is not installed. Run: pip install openai"
                )
            api_key = os.environ.get(self.API_KEY_ENV, "")
            if not api_key:
                raise RuntimeError(
                    f"{self.API_KEY_ENV} environment variable is not set"
                )
            client_kwargs: dict[str, Any] = {"api_key": api_key}
            if self.BASE_URL:
                client_kwargs["base_url"] = self.BASE_URL
            self._client = _openai.AsyncOpenAI(**client_kwargs)
        return self._client

    def build_request(
        self,
        config: ModelConfig,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> dict[str, Any]:
        api_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": (
                        msg.content
                        if isinstance(msg.content, str)
                        else json.dumps(msg.content)
                    ),
                })
            elif msg.role == "assistant" and msg.tool_calls:
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content if isinstance(msg.content, str) else None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        }
                        for call in msg.tool_calls
                    ],
                })
            else:
                api_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": api_messages,
            "max_tokens": config.max_tokens,
        }

        if config.temperature is not None:
            kwargs["temperature"] = config.temperature

        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        kwargs.update(config.extra)

        return kwargs

    def normalize_response(self, raw: Any) -> ModelResponse:
        choice = raw.choices[0]
        msg = choice.message

        content: str | None = msg.content
        tool_calls: list[ToolCall] | None = None

        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in msg.tool_calls
            ]

        finish_reason = _FINISH_REASON_MAP.get(
            choice.finish_reason or "stop", "stop"
        )

        usage: Usage | None = None
        if raw.usage:
            u = raw.usage
            usage = Usage(
                prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                total_tokens=getattr(u, "total_tokens", 0) or 0,
                cached_tokens=getattr(
                    getattr(u, "prompt_tokens_details", None),
                    "cached_tokens",
                    0,
                ) or 0,
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
        raw = await client.chat.completions.create(**kwargs)
        return self.normalize_response(raw)


class OpenAIAdapter(ChatCompletionsAdapter):
    """Direct OpenAI adapter (api.openai.com)."""

    API_KEY_ENV = "OPENAI_API_KEY"

    @property
    def provider_name(self) -> str:
        return "openai"


register_provider("openai", OpenAIAdapter)
