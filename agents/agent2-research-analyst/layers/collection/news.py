"""
SerpAPI-based news scraper.

Searches Google News for recent articles about the company.
Falls back gracefully if SerpAPI is not configured.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...config import ServiceConfig
from ...models import NewsArticle

logger = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"


class NewsScraper:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def search(self, company_name: str) -> list[NewsArticle]:
        if not self._config.serpapi.is_ready():
            logger.debug("NewsScraper: SerpAPI not configured, skipping")
            return []

        articles: list[NewsArticle] = []

        # Run two queries: company news + company funding/announcements
        queries = [
            company_name,
            f"{company_name} funding OR launch OR partnership OR announcement",
        ]

        async with httpx.AsyncClient(timeout=20.0) as client:
            for query in queries:
                batch = await self._search_query(client, query)
                articles.extend(batch)
                if len(articles) >= self._config.max_news_articles:
                    break

        # Deduplicate by URL
        seen: set[str] = set()
        unique: list[NewsArticle] = []
        for a in articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)

        logger.info("NewsScraper: found %d articles for '%s'", len(unique), company_name)
        return unique[: self._config.max_news_articles]

    async def _search_query(
        self, client: httpx.AsyncClient, query: str
    ) -> list[NewsArticle]:
        try:
            resp = await client.get(
                _SERPAPI_URL,
                params={
                    "engine": "google",
                    "q": query,
                    "tbm": "nws",
                    "num": "10",
                    "api_key": self._config.serpapi.api_key,
                },
            )

            if resp.status_code != 200:
                logger.warning("NewsScraper: HTTP %d for query '%s'", resp.status_code, query)
                return []

            data = resp.json()
            results = data.get("news_results") or []
            return [self._parse(r) for r in results if r.get("title")]

        except httpx.TimeoutException:
            logger.warning("NewsScraper: timeout for query '%s'", query)
            return []
        except Exception as exc:
            logger.warning("NewsScraper: error for query '%s' — %s", query, exc)
            return []

    @staticmethod
    def _parse(result: dict[str, Any]) -> NewsArticle:
        return NewsArticle(
            title=result.get("title", ""),
            snippet=result.get("snippet", ""),
            url=result.get("link", ""),
            date=result.get("date", ""),
            source=result.get("source", ""),
        )
