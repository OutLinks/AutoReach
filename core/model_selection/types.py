"""
Shared types for the model selection module.

All provider adapters normalize their responses to these types, so the rest
of the system never branches on which LLM is being used.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Message:
    """A single conversation message in provider-agnostic format."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    # Populated only for role="tool" — the id of the tool call being answered
    tool_call_id: str | None = None
    # Populated only for role="tool" — the tool name (required by some providers)
    name: str | None = None
    # Populated only for role="assistant" when the assistant requested tools.
    # Keeping this provider-neutral lets callers continue a multi-turn tool loop.
    tool_calls: list["ToolCall"] | None = None


@dataclass
class ToolDefinition:
    """
    A tool/function the model can invoke.

    `parameters` follows JSON Schema — it is translated to each provider's
    native format by the adapter (e.g. Anthropic calls it `input_schema`).
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str | None
    name: str
    arguments: str  # JSON-encoded string

    def parsed_arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments)


@dataclass
class Usage:
    """Token usage from an API response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class ModelResponse:
    """
    Normalized response from any provider.

    `content` is the text reply (None when the model only emitted tool calls).
    `tool_calls` is None when the model produced a plain text reply.
    `finish_reason` is always one of: "stop" | "tool_calls" | "length" | "content_filter".
    """

    content: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: str
    usage: Usage | None = None


@dataclass
class ModelConfig:
    """
    Complete configuration for one model call.

    Each agent owns its own ModelConfig. Different agents can specify
    different providers and models — the adapter is resolved at call time
    via `get_model(config)`.

    Example:
        config = ModelConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0.7,
        )

    `extra` is a dict of provider-specific overrides that are merged into
    the final request kwargs (e.g. {"top_p": 0.9} or Anthropic's
    {"thinking": {"type": "enabled", "budget_tokens": 5000}}).
    """

    provider: str           # "anthropic" | "openai" | "openrouter"
    model: str              # e.g. "claude-sonnet-4-6", "gpt-4o", "anthropic/claude-sonnet-4.6"
    max_tokens: int = 8192
    temperature: float | None = 0.7
    timeout: float = 60.0
    extra: dict[str, Any] = field(default_factory=dict)


def model_config_from_env(
    *,
    max_tokens: int,
    temperature: float | None,
    default_provider: str = "anthropic",
    default_model: str = "claude-sonnet-4-6",
) -> ModelConfig:
    """Build a model config with optional process-wide provider overrides."""
    configured_max = os.getenv("AUTOREACH_LLM_MAX_TOKENS", "").strip()
    if configured_max:
        max_tokens = min(max_tokens, max(1, int(configured_max)))
    return ModelConfig(
        provider=os.getenv("AUTOREACH_LLM_PROVIDER", default_provider).strip(),
        model=os.getenv("AUTOREACH_LLM_MODEL", default_model).strip(),
        max_tokens=max_tokens,
        temperature=temperature,
    )
