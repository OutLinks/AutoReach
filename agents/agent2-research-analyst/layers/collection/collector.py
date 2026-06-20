"""
Data Collection Layer orchestrator.

Runs all scrapers concurrently for a single lead and assembles the results
into a RawResearchData object. Each scraper is independent — a failure in one
does not block the others.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...config import ServiceConfig
from ...models import RawResearchData
from .website import WebsiteScraper
from .linkedin import LinkedInScraper
from .news import NewsScraper
from .social import SocialChecker

logger = logging.getLogger(__name__)


class DataCollector:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._website = WebsiteScraper(config)
        self._linkedin = LinkedInScraper(config)
        self._news = NewsScraper(config)
        self._social = SocialChecker(config)

    async def collect(self, lead: dict) -> RawResearchData:
        """
        Runs all scrapers concurrently and returns assembled raw data.
        `lead` is the raw dict from Agent 1's JSONL output.
        """
        data = RawResearchData()
        data.sources_attempted = list(self._config.enabled_collection_sources())

        # Extract fields from lead dict
        website = lead.get("website", "")
        linkedin_url = lead.get("linkedin_url", "")
        company_linkedin = lead.get("company_linkedin_url", "")
        company_name = lead.get("company_name", "")
        twitter = lead.get("twitter_handle", "")
        github = lead.get("github_handle", "")

        # Run all scrapers concurrently
        results = await asyncio.gather(
            self._collect_website(website),
            self._collect_person_linkedin(linkedin_url),
            self._collect_company_linkedin(company_linkedin),
            self._collect_news(company_name),
            self._collect_social(twitter, github, linkedin_url),
            return_exceptions=True,
        )

        website_pages, li_person, li_company, news, social = results

        # Assign results (log exceptions but don't raise)
        if isinstance(website_pages, dict):
            data.website_pages = website_pages
            if website_pages:
                data.sources_succeeded.append("firecrawl")
        elif isinstance(website_pages, Exception):
            logger.warning("DataCollector: website scraper failed — %s", website_pages)

        if isinstance(li_person, str) and li_person:
            data.linkedin_person = li_person
        if isinstance(li_company, str) and li_company:
            data.linkedin_company = li_company
        if (isinstance(li_person, str) and li_person) or (
            isinstance(li_company, str) and li_company
        ):
            data.sources_succeeded.append("proxycurl")

        if isinstance(news, list):
            data.news_articles = news
            if news:
                data.sources_succeeded.append("serpapi")
        elif isinstance(news, Exception):
            logger.warning("DataCollector: news scraper failed — %s", news)

        if isinstance(social, tuple):
            profiles, posts = social
            data.social_profiles = profiles
            data.social_posts = posts

        logger.info(
            "DataCollector: collected from %s (attempted: %s)",
            data.sources_succeeded,
            data.sources_attempted,
        )
        return data

    # ── Individual collectors (each returns a value or raises) ────────────────

    async def _collect_website(self, url: str) -> dict:
        if not url or not self._config.firecrawl.is_ready():
            return {}
        return await self._website.scrape(url)

    async def _collect_person_linkedin(self, url: str) -> str:
        if not url or not self._config.proxycurl.is_ready():
            return ""
        result = await self._linkedin.fetch_person(url)
        return result or ""

    async def _collect_company_linkedin(self, url: str) -> str:
        if not url or not self._config.proxycurl.is_ready():
            return ""
        result = await self._linkedin.fetch_company(url)
        return result or ""

    async def _collect_news(self, company_name: str) -> list:
        if not company_name or not self._config.serpapi.is_ready():
            return []
        return await self._news.search(company_name)

    async def _collect_social(
        self, twitter: str, github: str, linkedin_url: str
    ) -> tuple[dict, list]:
        profiles = await self._social.check_profiles(twitter or None, github or None)
        posts = await self._social.fetch_recent_posts(linkedin_url or "")
        return profiles, posts
