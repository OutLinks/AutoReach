"""
Data Collection Layer orchestrator.

Runs all scrapers concurrently for a single lead and assembles the results
into a RawResearchData object. Each scraper is independent — a failure in one
does not block the others.
"""

from __future__ import annotations

import asyncio
import logging

from ...config import ServiceConfig
from ...models import RawResearchData
from .website import WebsiteScraper
from .news import NewsScraper
from .social import SocialChecker
from .github import GitHubCollector
from .technology import TechnologyCollector
from .web_search import WebSearchCollector

logger = logging.getLogger(__name__)


class DataCollector:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._website = WebsiteScraper(config)
        self._news = NewsScraper(config)
        self._social = SocialChecker(config)
        self._web_search = WebSearchCollector(config)
        self._technology = TechnologyCollector(config)
        self._github = GitHubCollector(config)

    async def collect(self, lead: dict) -> RawResearchData:
        """
        Runs all scrapers concurrently and returns assembled raw data.
        `lead` is the raw dict from Agent 1's JSONL output.
        """
        data = RawResearchData()
        data.sources_attempted = list(self._config.enabled_collection_sources())

        # Extract fields from lead dict
        website = lead.get("website", "") or lead.get("company_website", "")
        if website and "website" not in data.sources_attempted:
            data.sources_attempted.append("website")
        company_name = lead.get("company_name", "")
        lead_name = lead.get("full_name", "") or " ".join(
            part for part in [lead.get("first_name", ""), lead.get("last_name", "")] if part
        ) or "Unknown"
        title = lead.get("title", "")
        twitter = lead.get("twitter_handle", "")
        github = lead.get("github_handle", "")

        # Run all scrapers concurrently
        results = await asyncio.gather(
            self._collect_website(website),
            self._collect_web_research(lead_name, title, company_name),
            self._collect_news(company_name),
            self._collect_social(twitter, github),
            self._collect_technology(website),
            self._collect_github(company_name, github),
            return_exceptions=True,
        )

        website_pages, web_research, news, social, technology, github_profile = results

        # Assign results (log exceptions but don't raise)
        if isinstance(website_pages, dict):
            data.website_pages = website_pages
            if website_pages:
                data.sources_succeeded.append(
                    "firecrawl" if self._config.firecrawl.is_ready() else "website"
                )
        elif isinstance(website_pages, Exception):
            logger.warning("DataCollector: website scraper failed — %s", website_pages)

        if isinstance(web_research, tuple):
            person_profile, company_profile, search_results = web_research
            data.public_person_profile = person_profile or None
            data.public_company_profile = company_profile or None
            data.web_search_results = search_results
            if person_profile or company_profile or search_results:
                data.sources_succeeded.append("tavily")
        elif isinstance(web_research, Exception):
            logger.warning("DataCollector: web research failed — %s", web_research)

        # If the lead has no site, let Tavily discover a public page and scrape
        # it so analysis has primary-source content rather than snippets alone.
        if not data.website_pages and data.web_search_results:
            fallback_url = next(
                (
                    result.url
                    for result in data.web_search_results
                    if result.url.startswith(("http://", "https://"))
                ),
                "",
            )
            if fallback_url:
                if "website" not in data.sources_attempted:
                    data.sources_attempted.append("website")
                fallback_pages = await self._website.scrape(fallback_url)
                if fallback_pages:
                    data.website_pages = fallback_pages
                    source = (
                        "firecrawl"
                        if self._config.firecrawl.is_ready()
                        else "website"
                    )
                    if source not in data.sources_succeeded:
                        data.sources_succeeded.append(source)

        if isinstance(news, list):
            data.news_articles = news
            if news:
                data.sources_succeeded.append("gnews")
        elif isinstance(news, Exception):
            logger.warning("DataCollector: news scraper failed — %s", news)

        if isinstance(social, tuple):
            profiles, posts = social
            data.social_profiles = profiles
            data.social_posts = posts

        if isinstance(technology, dict):
            data.technology_profile = technology
            if technology:
                data.sources_succeeded.append("wappalyzer")
        elif isinstance(technology, Exception):
            logger.warning("DataCollector: technology collector failed — %s", technology)

        if isinstance(github_profile, dict):
            data.github_profile = github_profile
            if github_profile:
                data.sources_succeeded.append("github")
        elif isinstance(github_profile, Exception):
            logger.warning("DataCollector: GitHub collector failed — %s", github_profile)

        logger.info(
            "DataCollector: collected from %s (attempted: %s)",
            data.sources_succeeded,
            data.sources_attempted,
        )
        return data

    # ── Individual collectors (each returns a value or raises) ────────────────

    async def _collect_website(self, url: str) -> dict:
        if not url:
            return {}
        return await self._website.scrape(url)

    async def _collect_web_research(
        self,
        lead_name: str,
        title: str,
        company_name: str,
    ) -> tuple:
        if not company_name or not self._config.tavily.is_ready():
            return "", "", []
        return await self._web_search.search(lead_name, title, company_name)

    async def _collect_news(self, company_name: str) -> list:
        if not company_name or not self._config.gnews.is_ready():
            return []
        return await self._news.search(company_name)

    async def _collect_social(
        self, twitter: str, github: str
    ) -> tuple[dict, list]:
        if not self._config.social_profile_checks_enabled:
            return {}, []
        profiles = await self._social.check_profiles(twitter or None, github or None)
        return profiles, []

    async def _collect_technology(self, website_url: str) -> dict:
        if not website_url or not self._config.wappalyzer.is_ready():
            return {}
        return await self._technology.detect(website_url)

    async def _collect_github(self, company_name: str, github_handle: str) -> dict:
        if not self._config.github.is_ready():
            return {}
        return await self._github.research(company_name, github_handle)
