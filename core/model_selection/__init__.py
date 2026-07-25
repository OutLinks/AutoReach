"""
Model selection module — provider-agnostic LLM interface for autoreach agents.

Every agent imports from here. No agent ever imports a provider module directly.
Different agents can use different providers and models by passing a different
ModelConfig — the adapter is resolved at call time.

Quick start:
    from core.model_selection import ModelConfig, get_model

    config = ModelConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        max_tokens=8192,
    )
    adapter = get_model(config)
    response = await adapter.complete(
        config,
        messages=[Message(role="user", content="Hello!")],
    )
    print(response.content)

Supported providers: "anthropic", "openai", "openrouter"

Required env vars per provider:
    anthropic   → ANTHROPIC_API_KEY
    openai      → OPENAI_API_KEY
    openrouter  → OPENROUTER_API_KEY

Adding a new provider:
    1. Create core/model_selection/providers/<name>.py
    2. Subclass ProviderAdapter (or ChatCompletionsAdapter for OpenAI-compatible)
    3. Call register_provider("<name>", YourAdapter) at module level
    4. Add `from . import <name>` to providers/__init__.py
"""

from __future__ import annotations

# Re-export shared types
from .types import (
    Message,
    ModelConfig,
    ModelResponse,
    ToolCall,
    ToolDefinition,
    Usage,
    model_config_from_env,
)

# Re-export the base class for type hints / subclassing
from .base import ProviderAdapter

# Re-export registry helpers
from .registry import get_adapter, list_providers, register_provider

# Trigger auto-registration of all built-in providers
from . import providers as _providers  # noqa: F401


def get_model(config: ModelConfig) -> ProviderAdapter:
    """
    Return the adapter for the provider named in `config`.

    This is the main entry point. Call it once per agent initialization
    and reuse the returned adapter across calls.

        adapter = get_model(config)
        response = await adapter.complete(config, messages)
    """
    return get_adapter(config.provider)


__all__ = [
    # Types
    "Message",
    "ModelConfig",
    "ModelResponse",
    "ToolCall",
    "ToolDefinition",
    "Usage",
    "model_config_from_env",
    # Base
    "ProviderAdapter",
    # Registry
    "get_adapter",
    "list_providers",
    "register_provider",
    # Main entry point
    "get_model",
]
