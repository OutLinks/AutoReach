"""
Provider adapter registry.

Adapters self-register at import time by calling `register_provider()`.
Callers use `get_adapter(provider_name)` to retrieve an instantiated adapter.

Auto-discovery happens when the `providers/` sub-package is imported — each
module in that package calls `register_provider()` at module level.
"""

from __future__ import annotations

from typing import Type

from .base import ProviderAdapter

_REGISTRY: dict[str, Type[ProviderAdapter]] = {}


def register_provider(name: str, adapter_cls: Type[ProviderAdapter]) -> None:
    """Register an adapter class under a canonical provider name.

    Later registrations silently replace earlier ones, allowing user-defined
    adapters to override built-ins.
    """
    _REGISTRY[name] = adapter_cls


def get_adapter(provider: str) -> ProviderAdapter:
    """Return a fresh adapter instance for the given provider name.

    Raises ValueError when no adapter is registered for that provider.
    """
    cls = _REGISTRY.get(provider)
    if cls is None:
        available = sorted(_REGISTRY)
        raise ValueError(
            f"Unknown provider: {provider!r}. "
            f"Registered providers: {available}"
        )
    return cls()


def list_providers() -> list[str]:
    """Return all registered provider names, sorted."""
    return sorted(_REGISTRY)
