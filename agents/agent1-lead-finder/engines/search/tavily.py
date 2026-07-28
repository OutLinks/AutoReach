"""
Tavily search adapter.

Uses AI-oriented web search to discover company websites and context. Results are
company-level leads; email/person enrichment happens in later pipeline stages.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.tavily.com/search"


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
        return re.sub(r"^www\.", "", host) or None
    except Exception:
        return None


class TavilyAdapter:
    """Searches the public web through Tavily."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, criteria: SearchCriteria, max_results: int) -> list[Lead]:
        query = self._build_query(criteria)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    _SEARCH_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "search_depth": "basic",
                        "max_results": min(max_results, 20),
                        "include_answer": True,
                        "include_raw_content": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Tavily HTTP %s for query %r", exc.response.status_code, query)
            return []
        except Exception as exc:
            logger.error("Tavily request failed for query %r: %s", query, exc)
            return []

        leads = [
            self._map_result(result, criteria, data.get("answer"))
            for result in (data.get("results") or [])
            if result.get("url")
        ]
        logger.info("Tavily found %d web results", len(leads))
        return leads[:max_results]

    @staticmethod
    def _build_query(criteria: SearchCriteria) -> str:
        pieces = criteria.keywords + criteria.industries + criteria.job_titles
        query = " ".join(pieces).strip() or "businesses"
        if criteria.locations:
            query = f"{query} {' OR '.join(criteria.locations)}"
        if criteria.technologies:
            query = f"{query} using {' OR '.join(criteria.technologies)}"
        return f"{query} official company website contact email"

    @staticmethod
    def _map_result(
        result: dict[str, Any],
        criteria: SearchCriteria,
        answer: str | None,
    ) -> Lead:
        url = result.get("url")
        title = (result.get("title") or "").strip()
        content = result.get("content") or answer

        return Lead(
            company_name=title or _extract_domain(url),
            company_website=url,
            company_domain=_extract_domain(url),
            company_description=content,
            industry=(criteria.industries or [None])[0],
            sources=["tavily"],
            raw_data={"tavily": result},
            stage="raw",
        )
