"""
OpenRouter provider adapter.

OpenRouter is OpenAI-compatible but:
  - Targets https://openrouter.ai/api/v1
  - Uses OPENROUTER_API_KEY
  - Model names are namespaced: "anthropic/claude-sonnet-4.6", "openai/gpt-4o"
  - Supports provider preferences and per-request routing via extra_body

Example ModelConfig:
    ModelConfig(
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        extra={
            "extra_body": {
                "provider": {"order": ["Anthropic"], "allow_fallbacks": False}
            }
        }
    )
"""

from __future__ import annotations

from ..registry import register_provider
from .openai import ChatCompletionsAdapter


class OpenRouterAdapter(ChatCompletionsAdapter):
    """Adapter for OpenRouter — unified API for 200+ models."""

    BASE_URL = "https://openrouter.ai/api/v1"
    API_KEY_ENV = "OPENROUTER_API_KEY"

    @property
    def provider_name(self) -> str:
        return "openrouter"


register_provider("openrouter", OpenRouterAdapter)
