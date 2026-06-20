"""
Service configuration for Agent 1: Lead Finder.

Each API service has an `enabled` flag and an `api_key` field.
Toggle `enabled = False` to turn an API off system-wide — the system prompt
sent to the LLM will omit that service, and the engines will skip it.

Load from environment variables or override programmatically:

    config = ServiceConfig()              # reads from env vars
    config.apollo.enabled = False         # disable Apollo for this run
    config.hunter.enabled = False         # disable verification
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.model_selection import ModelConfig


@dataclass
class APIConfig:
    """Credentials + toggle for a single external API."""

    api_key: str = ""
    enabled: bool = True

    def is_ready(self) -> bool:
        """True only when the service is enabled AND a key is configured."""
        return self.enabled and bool(self.api_key.strip())


@dataclass
class ServiceConfig:
    """
    Master configuration for Agent 1.

    Each section maps to one engine. Flip `enabled = False` to remove
    an API from both the system prompt and the execution pipeline.

    Environment variables (set in .env or shell):
        APOLLO_API_KEY, PRODUCTHUNT_API_KEY,
        CLEARBIT_API_KEY,
        HUNTER_API_KEY,
        REDIS_URL,
        OPENROUTER_API_KEY (for the LLM)
    """

    # ── Search APIs (Module 1) ─────────────────────────────────────────────
    apollo: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("APOLLO_API_KEY", ""),
        enabled=True,
    ))
    producthunt: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("PRODUCTHUNT_API_KEY", ""),
        enabled=True,
    ))

    # ── Enrich APIs (Module 2) ─────────────────────────────────────────────
    clearbit: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("CLEARBIT_API_KEY", ""),
        enabled=True,
    ))

    # ── Verify APIs (Module 3) ─────────────────────────────────────────────
    hunter: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("HUNTER_API_KEY", ""),
        enabled=True,
    ))

    # ── LLM ───────────────────────────────────────────────────────────────
    model: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="openrouter",
        model="openrouter/owl-alpha",
        max_tokens=4096,
        temperature=0.1,    # low temp: deterministic JSON extraction
    ))

    # ── Pipeline behaviour ─────────────────────────────────────────────────
    max_leads_per_run: int = 50
    concurrency: int = 5            # max concurrent API calls per engine

    # ── Redis ─────────────────────────────────────────────────────────────
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379")
    )
    redis_ttl: int = 86400          # 24 h — how long job data stays in Redis

    # ── Helpers ────────────────────────────────────────────────────────────

    def enabled_search_apis(self) -> list[str]:
        apis = []
        if self.apollo.is_ready():
            apis.append("apollo")
        if self.producthunt.is_ready():
            apis.append("producthunt")
        return apis

    def enabled_enrich_apis(self) -> list[str]:
        apis = []
        if self.clearbit.is_ready():
            apis.append("clearbit")
        return apis

    def enabled_verify_apis(self) -> list[str]:
        apis = []
        if self.hunter.is_ready():
            apis.append("hunter")
        return apis

    def any_search_api_ready(self) -> bool:
        return bool(self.enabled_search_apis())
