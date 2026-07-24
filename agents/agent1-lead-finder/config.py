"""
Service configuration for Agent 1: Lead Finder.

The lead finder is scrape-first: it discovers companies from public URLs and
directories supplied in the prompt or `LEAD_FINDER_SOURCE_URLS`. External APIs
are optional enrichments and are disabled by default. Toggle an API's
`enabled` flag to opt in; it will still run only when a key is configured.

Load from environment variables or override programmatically:

    config = ServiceConfig()              # reads from env vars
    config.tavily.enabled = True          # opt in to Tavily discovery
    config.hunter.enabled = True          # opt in to Hunter enrichment
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

    Public web scraping is the default discovery method. Give it company URLs
    or pages that link directly to company websites, either in the user prompt
    or with `LEAD_FINDER_SOURCE_URLS` (comma- or newline-separated).

    Every third-party API starts disabled. Flip its `enabled` flag to `True`
    only when that paid/credentialed capability is wanted.

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
        enabled=False,
    ))
    tavily: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("TAVILY_API_KEY", ""),
        enabled=False,
    ))

    # ── Enrich APIs (Module 2) ─────────────────────────────────────────────
    hunter: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("HUNTER_API_KEY", ""),
        enabled=False,
    ))
    wappalyzer: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("WAPPALYZER_API_KEY", ""),
        enabled=False,
    ))
    crunchbase: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("CRUNCHBASE_API_KEY", ""),
        enabled=False,
    ))
    whoisxml: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("WHOISXML_API_KEY", ""),
        enabled=False,
    ))
    securitytrails: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("SECURITYTRAILS_API_KEY", ""),
        enabled=False,
    ))

    # ── Verify APIs (Module 3) ─────────────────────────────────────────────
    abstract: APIConfig = field(default_factory=lambda: APIConfig(
        api_key=os.environ.get("ABSTRACT_API_KEY", ""),
        enabled=False,
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

    # ── Public web scraping (default discovery path) ───────────────────────
    web_scraper_enabled: bool = True
    web_scraper_seed_urls: list[str] = field(default_factory=lambda: [
        url.strip()
        for url in os.environ.get("LEAD_FINDER_SOURCE_URLS", "").replace("\n", ",").split(",")
        if url.strip()
    ])

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
        return self.web_scraper_enabled or bool(self.enabled_search_apis())
