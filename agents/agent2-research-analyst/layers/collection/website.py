"""
Firecrawl-based website scraper.

Scrapes a configurable list of pages (homepage + about + services etc.)
and returns their markdown content keyed by page slug.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"


class WebsiteScraper:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def scrape(self, website_url: str) -> dict[str, str]:
        """
        Returns dict of page_slug → markdown text.
        On any error the page is skipped; never raises.
        """
        if not self._config.firecrawl.is_ready():
            logger.debug("WebsiteScraper: Firecrawl not configured, skipping")
            return {}

        url = website_url.rstrip("/")
        pages_to_try = self._config.website_pages_to_scrape[: self._config.max_pages_per_site]

        results: dict[str, str] = {}

        async with httpx.AsyncClient(timeout=30.0) as client:
            for page_slug in pages_to_try:
                page_url = f"{url}/{page_slug}" if page_slug else url
                slug_key = page_slug or "homepage"
                content = await self._scrape_page(client, page_url)
                if content:
                    results[slug_key] = content

        logger.info(
            "WebsiteScraper: scraped %d/%d pages from %s",
            len(results),
            len(pages_to_try),
            url,
        )
        return results

    async def _scrape_page(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        try:
            resp = await client.post(
                _FIRECRAWL_SCRAPE_URL,
                headers={
                    "Authorization": f"Bearer {self._config.firecrawl.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "removeTags": ["nav", "footer", "script", "style", "header"],
                },
            )

            if resp.status_code == 404:
                return None

            if resp.status_code != 200:
                logger.warning(
                    "WebsiteScraper: HTTP %d for %s", resp.status_code, url
                )
                return None

            data = resp.json()
            content = data.get("data", {}).get("markdown", "") or ""
            return content[: self._config.max_website_chars] if content.strip() else None

        except httpx.TimeoutException:
            logger.warning("WebsiteScraper: timeout for %s", url)
            return None
        except Exception as exc:
            logger.warning("WebsiteScraper: error for %s — %s", url, exc)
            return None
