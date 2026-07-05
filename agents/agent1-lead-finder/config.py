"""
Service configuration for Agent 1: Lead Finder.

Each API service has an `enabled` flag and an `api_key` field.
Toggle `enabled = False` to turn an API off system-wide — the system prompt
sent to the LLM will omit that service, and the engines will skip it.

Load from environment variables or override programmatically:

    config = ServiceConfig()              # reads from env vars
    config.google_places.enabled = False  # disable Places discovery for this run
    config.abstract.enabled = False       # disable email verification
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.env import load_dotenv
from core.model_selection import ModelConfig

load_dotenv()


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
        GOOGLE_PLACES_API_KEY, TAVILY_API_KEY,
        HUNTER_API_KEY, ABSTRACT_API_KEY,
        WAPPALYZER_API_KEY, CRUNCHBASE_API_KEY,
        WHOISXML_API_KEY, SECURITYTRAILS_API_KEY,
        REDIS_URL,
        ANTHROPIC_API_KEY / OPENAI_API_KEY (for the LLM)
    """

    # ── Search APIs (Module 1) ─────────────────────────────────────────────
    google_places: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("GOOGLE_PLACES_API_KEY", ""),
        enabled=True,
    ))
    tavily: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("TAVILY_API_KEY", ""),
        enabled=True,
    ))

    # ── Enrich APIs (Module 2) ─────────────────────────────────────────────
    hunter: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("HUNTER_API_KEY", ""),
        enabled=True,
    ))
    wappalyzer: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("WAPPALYZER_API_KEY", ""),
        enabled=True,
    ))
    crunchbase: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("CRUNCHBASE_API_KEY", ""),
        enabled=True,
    ))
    whoisxml: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("WHOISXML_API_KEY", ""),
        enabled=True,
    ))
    securitytrails: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("SECURITYTRAILS_API_KEY", ""),
        enabled=True,
    ))

    # ── Verify APIs (Module 3) ─────────────────────────────────────────────
    abstract: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("ABSTRACT_API_KEY", ""),
        enabled=True,
    ))

    # ── LLM ───────────────────────────────────────────────────────────────
    model: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
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
        if self.google_places.is_ready():
            apis.append("google_places")
        if self.tavily.is_ready():
            apis.append("tavily")
        return apis

    def enabled_enrich_apis(self) -> list[str]:
        apis = []
        if self.hunter.is_ready():
            apis.append("hunter_domain_search")
        if self.wappalyzer.is_ready():
            apis.append("wappalyzer")
        if self.crunchbase.is_ready():
            apis.append("crunchbase")
        if self.whoisxml.is_ready():
            apis.append("whoisxml")
        if self.securitytrails.is_ready():
            apis.append("securitytrails")
        return apis

    def enabled_verify_apis(self) -> list[str]:
        apis = []
        if self.abstract.is_ready():
            apis.append("abstract")
        return apis

    def any_search_api_ready(self) -> bool:
        return bool(self.enabled_search_apis())
