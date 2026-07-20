"""
Configuration for Agent 2: Research Analyst.

The MVP is Firecrawl-only for data collection: it scrapes the lead's public
website and passes that evidence to the LLM analysis layer. Tavily, GNews,
GitHub, Wappalyzer, and direct social-profile checks are optional enhancements
and start disabled, even if their environment variables are present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.env import load_dotenv
from core.model_selection.types import ModelConfig

load_dotenv()


@dataclass
class APIConfig:
    api_key: str = ""
    enabled: bool = True

    def is_ready(self) -> bool:
        return self.enabled and bool(self.api_key.strip())


@dataclass
class ServiceConfig:
    # Tailored by the Orchestrator Campaign Planner for a single live run.
    campaign_instruction: str = ""

    # ── MVP website scraping ────────────────────────────────────────────────
    firecrawl: APIConfig = field(
        default_factory=lambda: APIConfig(
            api_key=os.getenv("FIRECRAWL_API_KEY", ""), enabled=True
        )
    )

    # ── Optional enrichment APIs (disabled for the MVP) ─────────────────────
    tavily: APIConfig = field(
        default_factory=lambda: APIConfig(
            api_key=os.getenv("TAVILY_API_KEY", ""), enabled=False
        )
    )
    gnews: APIConfig = field(
        default_factory=lambda: APIConfig(
            api_key=os.getenv("GNEWS_API_KEY", ""), enabled=False
        )
    )
    github: APIConfig = field(
        default_factory=lambda: APIConfig(
            api_key=os.getenv("GITHUB_API_KEY", ""), enabled=False
        )
    )
    wappalyzer: APIConfig = field(
        default_factory=lambda: APIConfig(
            api_key=os.getenv("WAPPALYZER_API_KEY", ""), enabled=False
        )
    )
    social_profile_checks_enabled: bool = False

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
    max_news_articles: int = 10          # max GNews results per company
    max_web_results: int = 8             # max Tavily results per company/person
    max_website_chars: int = 40_000      # truncation limit for LLM context
    max_public_profile_chars: int = 10_000

    # Firecrawl pages to scrape (slugs appended to base URL)
    website_pages_to_scrape: list[str] = field(
        default_factory=lambda: ["", "about", "services", "pricing", "blog", "team"]
    )

    def enabled_collection_sources(self) -> list[str]:
        sources = []
        if self.firecrawl.is_ready():
            sources.append("firecrawl")
        if self.tavily.is_ready():
            sources.append("tavily")
        if self.gnews.is_ready():
            sources.append("gnews")
        if self.github.is_ready():
            sources.append("github")
        if self.wappalyzer.is_ready():
            sources.append("wappalyzer")
        return sources

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls()
