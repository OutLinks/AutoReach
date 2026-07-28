"""
Public website scraper with optional Firecrawl extraction.

Scrapes a configurable list of pages (homepage + about + services etc.)
and returns their markdown content keyed by page slug.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Optional
from urllib.parse import urlparse

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
_USER_AGENT = "AutoReachResearch/0.1 (+public-business-site-research)"


class _ReadablePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._in_title = False
        self.title = ""
        self.description = ""
        self.text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            attributes = {key.lower(): value or "" for key, value in attrs}
            marker = (
                attributes.get("name") or attributes.get("property") or ""
            ).lower()
            if marker in {"description", "og:description"} and not self.description:
                self.description = attributes.get("content", "").strip()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = " ".join(unescape(data).split())
        if not value:
            return
        if self._in_title:
            self.title = f"{self.title} {value}".strip()
        self.text.append(value)

    def readable_text(self) -> str:
        parts = [self.title, self.description, *self.text]
        return re.sub(
            r"\s+",
            " ",
            " ".join(part for part in parts if part),
        ).strip()


class WebsiteScraper:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def scrape(self, website_url: str) -> dict[str, str]:
        """
        Returns dict of page_slug → markdown text.
        On any error the page is skipped; never raises.
        """
        url = website_url.rstrip("/")
        pages_to_try = self._pages_to_try(url)

        results: dict[str, str] = {}

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
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

    def _pages_to_try(self, url: str) -> list[str]:
        """Do not append company-site slugs to a specific job or careers URL."""
        parsed = urlparse(url)
        path = parsed.path.lower().rstrip("/")
        job_hosts = {
            "jobs.ashbyhq.com",
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "jobs.lever.co",
        }
        is_job_page = (
            parsed.hostname in job_hosts
            or any(part in path.split("/") for part in ("career", "careers", "job", "jobs"))
        )
        if is_job_page:
            return [""]
        return self._config.website_pages_to_scrape[: self._config.max_pages_per_site]

    async def _scrape_page(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        if not self._config.firecrawl.is_ready():
            return await self._scrape_page_direct(client, url)
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
                },
            )

            if resp.status_code == 404:
                return None

            if resp.status_code != 200:
                logger.warning(
                    "WebsiteScraper: HTTP %d for %s — %s",
                    resp.status_code,
                    url,
                    resp.text[:500],
                )
                return None

            data = resp.json()
            content = data.get("data", {}).get("markdown", "") or ""
            if "job not found" in content.lower():
                logger.warning(
                    "WebsiteScraper: source no longer contains an active job — %s",
                    url,
                )
                return None
            return content[: self._config.max_website_chars] if content.strip() else None

        except httpx.TimeoutException:
            logger.warning("WebsiteScraper: timeout for %s", url)
            return None
        except Exception as exc:
            logger.warning("WebsiteScraper: error for %s — %s", url, exc)
            return None

    async def _scrape_page_direct(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> Optional[str]:
        if not self._is_public_http_url(url):
            logger.warning("WebsiteScraper: refusing non-public URL — %s", url)
            return None
        try:
            response = await client.get(url)
            if not self._is_public_http_url(str(response.url)):
                logger.warning(
                    "WebsiteScraper: refusing non-public redirect — %s",
                    response.url,
                )
                return None
            if response.status_code != 200:
                logger.warning(
                    "WebsiteScraper: direct HTTP %d for %s",
                    response.status_code,
                    url,
                )
                return None
            if "html" not in response.headers.get("content-type", "").lower():
                return None
            parser = _ReadablePageParser()
            parser.feed(response.text)
            content = parser.readable_text()
            if "job not found" in content.lower():
                logger.warning(
                    "WebsiteScraper: source no longer contains an active job — %s",
                    url,
                )
                return None
            return content[: self._config.max_website_chars] if content else None
        except httpx.TimeoutException:
            logger.warning("WebsiteScraper: direct timeout for %s", url)
            return None
        except Exception as exc:
            logger.warning("WebsiteScraper: direct error for %s — %s", url, exc)
            return None

    @staticmethod
    def _is_public_http_url(url: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not hostname:
            return False
        if hostname == "localhost" or hostname.endswith((".local", ".internal")):
            return False
        try:
            address = ip_address(hostname)
        except ValueError:
            return True
        return address.is_global
