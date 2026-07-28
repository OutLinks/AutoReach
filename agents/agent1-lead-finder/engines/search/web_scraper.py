"""Public-web, scrape-first lead discovery.

This adapter deliberately does not query a search-engine results page. It
starts from company sites or curated directory/list pages supplied by the user
or configuration, follows their direct external company links, and extracts
only publicly displayed company/contact information.
"""

from __future__ import annotations

import asyncio
import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SKIP_DOMAINS = frozenset({
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "google.com", "apple.com", "microsoft.com", "cloudflare.com",
    "startupschool.org", "news.ycombinator.com", "bookface.ycombinator.com",
})
_CONTACT_PATH_MARKERS = (
    "/about", "/company", "/contact", "/team", "/support", "/help",
)
_SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip",
    ".css", ".js", ".xml", ".ico",
)
_MAX_CONCURRENT_SITES = 10
_USER_AGENT = "AutoReachLeadFinder/0.1 (+public-business-site-crawler)"


class _PageParser(HTMLParser):
    """Small dependency-free extractor for links and useful page metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text: list[str] = []
        self.title = ""
        self.description = ""
        self.site_name = ""
        self.og_title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content", "").strip()
            if key in {"description", "og:description"} and content and not self.description:
                self.description = content
            elif key in {"og:title", "twitter:title"} and content and not self.og_title:
                self.og_title = content
            elif key == "og:site_name" and content:
                self.site_name = content

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title = f"{self.title} {value}".strip()
        self.text.append(value)


class WebScraperAdapter:
    """Builds company leads from explicitly supplied public web pages."""

    async def search(
        self,
        criteria: SearchCriteria,
        source_urls: Iterable[str],
        max_results: int,
    ) -> list[Lead]:
        seeds = self._unique_urls(source_urls)[:100]
        if not seeds:
            return []

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            seed_pages = await asyncio.gather(
                *(self._fetch(client, url) for url in seeds), return_exceptions=True
            )

            direct_candidates: list[str] = []
            discovered_candidates: list[str] = []
            for seed, page in zip(seeds, seed_pages):
                if isinstance(page, _PageParser):
                    direct_candidates.append(seed)
                    discovered_candidates.extend(
                        self._external_company_links(seed, page.links)
                    )

            # Crawl beyond the requested result count so directory/navigation
            # pages without a public company-domain email can be discarded
            # without starving valid company links that appear later.
            crawl_limit = min(max(max_results * 5, 20), 100)
            candidates = self._unique_urls(
                [*direct_candidates, *discovered_candidates]
            )[:crawl_limit]
            leads = await self._scrape_candidates_with_client(
                client,
                criteria,
                candidates,
                max_results,
            )

        logger.info(
            "Web scraper found %d email-qualified companies from %d source URL(s)",
            len(leads),
            len(seeds),
        )
        return leads

    async def scrape_companies(
        self,
        criteria: SearchCriteria,
        company_urls: Iterable[str],
        max_results: int,
    ) -> list[Lead]:
        """Scrape web-search candidate sites and keep public-email leads only."""
        candidates = self._unique_urls(company_urls)
        if not candidates:
            return []
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            return await self._scrape_candidates_with_client(
                client,
                criteria,
                candidates,
                max_results,
            )

    async def _scrape_candidates_with_client(
        self,
        client: httpx.AsyncClient,
        criteria: SearchCriteria,
        candidates: list[str],
        max_results: int,
    ) -> list[Lead]:
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SITES)

        async def scrape(url: str) -> Lead | None:
            async with semaphore:
                return await self._scrape_one(client, criteria, url)

        results = await asyncio.gather(
            *(scrape(url) for url in candidates),
            return_exceptions=True,
        )
        leads = [
            item
            for item in results
            if isinstance(item, Lead)
        ]
        return leads[:max_results]

    async def _scrape_one(
        self,
        client: httpx.AsyncClient,
        criteria: SearchCriteria,
        url: str,
    ) -> Lead | None:
        page = await self._fetch(client, url)
        if not isinstance(page, _PageParser):
            return None

        detail_urls = self._contact_page_urls(url, page.links)[:3]
        detail_pages = await asyncio.gather(
            *(self._fetch(client, detail_url) for detail_url in detail_urls),
            return_exceptions=True,
        )
        pages: list[tuple[str, _PageParser]] = [(url, page)]
        pages.extend(
            (detail_url, detail_page)
            for detail_url, detail_page in zip(detail_urls, detail_pages)
            if isinstance(detail_page, _PageParser)
        )
        return self._map_pages(url, pages, criteria)

    @staticmethod
    async def _fetch(client: httpx.AsyncClient, url: str) -> _PageParser | None:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Could not scrape %s: %s", url, exc)
            return None

        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            logger.info("Skipping non-HTML source %s", url)
            return None

        parser = _PageParser()
        parser.feed(response.text)
        return parser

    @classmethod
    def _external_company_links(cls, source_url: str, links: Iterable[str]) -> list[str]:
        source_domain = cls._domain(source_url)
        candidates: list[str] = []
        for href in links:
            url = cls._normalise_url(urljoin(source_url, href))
            if not url or cls._domain(url) == source_domain or not cls._is_company_url(url):
                continue
            candidates.append(url)
        return candidates

    @classmethod
    def _contact_page_urls(
        cls,
        company_url: str,
        links: Iterable[str],
    ) -> list[str]:
        company_domain = cls._domain(company_url)
        candidates: list[str] = []
        for href in links:
            url = cls._normalise_url(urljoin(company_url, href))
            if not url or cls._domain(url) != company_domain:
                continue
            path = urlparse(url).path.lower().rstrip("/")
            if any(marker in path for marker in _CONTACT_PATH_MARKERS):
                candidates.append(url)
        return [
            url
            for url in cls._unique_urls(candidates)
            if url != cls._normalise_url(company_url)
        ]

    @classmethod
    def _unique_urls(cls, urls: Iterable[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for raw_url in urls:
            url = cls._normalise_url(raw_url)
            if not url or url in seen or not cls._is_company_url(url):
                continue
            seen.add(url)
            unique.append(url)
        return unique

    @staticmethod
    def _normalise_url(raw_url: str) -> str | None:
        raw_url = raw_url.strip()
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        # Fragments are page-local and query strings often contain trackers.
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))

    @staticmethod
    def _domain(url: str) -> str:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")

    @classmethod
    def _is_company_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        domain = cls._domain(url)
        return bool(
            parsed.scheme in {"http", "https"}
            and domain
            and not any(
                domain == skipped or domain.endswith(f".{skipped}")
                for skipped in _SKIP_DOMAINS
            )
            and not any(parsed.path.lower().endswith(suffix) for suffix in _SKIP_SUFFIXES)
        )

    @classmethod
    def _map_pages(
        cls,
        url: str,
        pages: list[tuple[str, _PageParser]],
        criteria: SearchCriteria,
    ) -> Lead | None:
        if not pages:
            return None
        page = pages[0][1]
        text = unescape(
            " ".join(
                text_part
                for _, parsed_page in pages
                for text_part in parsed_page.text
            )
        )
        company_name = (
            page.site_name
            or cls._company_name_hint(url)
            or cls._clean_title(page.og_title)
            or cls._clean_title(page.title)
            or cls._domain(url)
        )
        if not company_name:
            return None

        domain = cls._domain(url)
        emails = cls._public_emails(text, domain)
        if not emails:
            return None
        description = next(
            (
                parsed_page.description
                for _, parsed_page in pages
                if parsed_page.description
            ),
            "",
        ) or text[:500]
        return Lead(
            company_name=company_name,
            company_website=url,
            company_domain=domain,
            company_description=description or None,
            email=emails[0],
            title=cls._job_title_hint(url, page),
            industry=(criteria.industries or [None])[0],
            website_reachable=True,
            sources=["web_scraper"],
            raw_data={
                "web_scraper": {
                    "url": url,
                    "title": page.title,
                    "source_urls": [source_url for source_url, _ in pages],
                }
            },
            stage="raw",
        )

    @staticmethod
    def _clean_title(title: str) -> str:
        # Titles commonly use a brand followed by a tagline or page title.
        return re.split(r"\s[|–—-]\s", title.strip(), maxsplit=1)[0].strip()

    @classmethod
    def _company_name_hint(cls, url: str) -> str:
        """Infer a readable company name from direct company and ATS URLs."""
        parsed = urlparse(url)
        domain = cls._domain(url)
        if domain == "jobs.ashbyhq.com":
            parts = [part for part in parsed.path.split("/") if part]
            slug = parts[0] if parts else ""
            for suffix in ("careers", "career", "jobs", "hiring"):
                if slug.lower().endswith(suffix) and len(slug) > len(suffix):
                    slug = slug[: -len(suffix)]
                    break
            return cls._humanize_slug(slug)

        label = domain.split(".", 1)[0]
        return cls._humanize_slug(label)

    @staticmethod
    def _humanize_slug(slug: str) -> str:
        words = re.sub(r"[_-]+", " ", slug).strip()
        if not words:
            return ""
        brand_names = {
            "openrouter": "OpenRouter",
        }
        return brand_names.get(words.lower(), words.title())

    @classmethod
    def _job_title_hint(cls, url: str, page: _PageParser) -> str | None:
        domain = cls._domain(url)
        path = urlparse(url).path.lower()
        if domain != "jobs.ashbyhq.com" and not any(
            marker in path for marker in ("/career", "/jobs/", "/job/")
        ):
            return None
        title = cls._clean_title(page.og_title or page.title)
        return title if title and title.lower() not in {"jobs", "careers"} else None

    @staticmethod
    def _public_emails(text: str, company_domain: str) -> list[str]:
        emails = list(dict.fromkeys(email.lower() for email in _EMAIL_RE.findall(text)))
        return [
            email
            for email in emails
            if (
                (email_domain := email.rsplit("@", 1)[-1]) == company_domain
                or email_domain.endswith(f".{company_domain}")
            )
        ]
