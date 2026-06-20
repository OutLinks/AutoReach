"""
Configuration for Agent 2: Research Analyst.

All API keys are read from environment variables. Setting a key to "" or
leaving the env var unset disables that service; the system prompt and
collection layer adapt automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.model_selection.types import ModelConfig


@dataclass
class APIConfig:
    api_key: str = ""
    enabled: bool = True

    def is_ready(self) -> bool:
        return self.enabled and bool(self.api_key.strip())


@dataclass
class ServiceConfig:
    # Data Collection APIs
    firecrawl: APIConfig = field(
        default_factory=lambda: APIConfig(api_key=os.getenv("FIRECRAWL_API_KEY", ""))
    )
    proxycurl: APIConfig = field(
        default_factory=lambda: APIConfig(api_key=os.getenv("PROXYCURL_API_KEY", ""))
    )
    serpapi: APIConfig = field(
        default_factory=lambda: APIConfig(api_key=os.getenv("SERPAPI_API_KEY", ""))
    )

    # Model configuration
    model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0.3,
        )
    )

    # Operational limits
    concurrency: int = 3                 # max leads researched in parallel
    max_pages_per_site: int = 5          # max pages Firecrawl scrapes per domain
    max_news_articles: int = 10          # max SerpAPI results per company
    max_website_chars: int = 40_000      # truncation limit for LLM context
    max_linkedin_chars: int = 10_000

    # Firecrawl pages to scrape (slugs appended to base URL)
    website_pages_to_scrape: list[str] = field(
        default_factory=lambda: ["", "about", "services", "pricing", "blog", "team"]
    )

    def enabled_collection_sources(self) -> list[str]:
        sources = []
        if self.firecrawl.is_ready():
            sources.append("firecrawl")
        if self.proxycurl.is_ready():
            sources.append("proxycurl")
        if self.serpapi.is_ready():
            sources.append("serpapi")
        return sources

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls()
