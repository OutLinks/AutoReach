"""
Abstract base for provider adapters.

A ProviderAdapter owns:
  - build_request  : translate messages + tools into the provider's native kwargs
  - normalize_response : normalize raw SDK response into ModelResponse
  - complete : make the async API call end-to-end

It does NOT own: retry logic, streaming, credential rotation, or caching.
Those live at a higher layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import Message, ModelConfig, ModelResponse, ToolDefinition


class ProviderAdapter(ABC):
    """Base class for all LLM provider adapters."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Canonical provider identifier, e.g. 'anthropic'."""
        ...

    @abstractmethod
    def build_request(
        self,
        config: ModelConfig,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> dict[str, Any]:
        """
        Translate messages + tools into the provider's native request kwargs.

        The returned dict is unpacked directly into the SDK call, so every
        key must be a valid parameter name for that provider's SDK.

        This method handles all format differences between providers:
          - Anthropic: system message extracted to top-level `system` param,
            tools use `input_schema`, tool result messages use `tool_result` blocks
          - OpenAI / OpenRouter: system is a message with role="system",
            tools use `function.parameters`, tool results use role="tool"
        """
        ...

    @abstractmethod
    def normalize_response(self, raw: Any) -> ModelResponse:
        """
        Normalize the raw SDK response object into a ModelResponse.

        All callers receive the same ModelResponse type regardless of which
        provider produced the response.
        """
        ...

    @abstractmethod
    async def complete(
        self,
        config: ModelConfig,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> ModelResponse:
        """
        Make a completion call and return a normalized ModelResponse.

        Typical implementation:
            kwargs = self.build_request(config, messages, tools)
            raw = await self._get_client().create(**kwargs)
            return self.normalize_response(raw)
        """
        ...
