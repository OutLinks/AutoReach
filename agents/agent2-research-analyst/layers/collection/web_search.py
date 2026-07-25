"""Tavily-powered public web research collector."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...config import ServiceConfig
from ...models import SearchResult

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"


class WebSearchCollector:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def search(self, lead_name: str, title: str, company_name: str) -> tuple[str, str, list[SearchResult]]:
        if not self._config.tavily.is_ready():
            return "", "", []

        company_query = f"{company_name} company overview pain points competitors"
        person_query = " ".join(part for part in [lead_name, title, company_name] if part)

        company_results = await self._query(company_query)
        person_results = await self._query(person_query) if lead_name != "Unknown" else []

        company_profile = self._summarize(company_results)
        person_profile = self._summarize(person_results)
        return person_profile, company_profile, (company_results + person_results)[: self._config.max_web_results]

    async def _query(self, query: str) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    _TAVILY_URL,
                    headers={
                        "Authorization": f"Bearer {self._config.tavily.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "search_depth": "basic",
                        "max_results": self._config.max_web_results,
                        "include_answer": True,
                        "include_raw_content": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("WebSearchCollector: Tavily HTTP %s for %r", exc.response.status_code, query)
            return []
        except Exception as exc:
            logger.warning("WebSearchCollector: Tavily error for %r — %s", query, exc)
            return []

        results = [
            SearchResult(
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                url=item.get("url", ""),
                source="tavily",
            )
            for item in (data.get("results") or [])
            if item.get("url")
        ]
        answer = data.get("answer")
        if answer:
            results.insert(0, SearchResult(title="Tavily summary", snippet=answer, url="", source="tavily"))
        return results

    @staticmethod
    def _summarize(results: list[SearchResult]) -> str:
        return "\n".join(
            f"- {result.title}: {result.snippet} ({result.url})"
            for result in results
            if result.snippet
        )
