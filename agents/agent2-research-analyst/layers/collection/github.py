"""GitHub research collector."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_GITHUB_SEARCH_REPOS = "https://api.github.com/search/repositories"
_GITHUB_SEARCH_USERS = "https://api.github.com/search/users"


class GitHubCollector:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def research(self, company_name: str, github_handle: str = "") -> dict[str, Any]:
        if not self._config.github.is_ready() or not (company_name or github_handle):
            return {}

        headers = {
            "Authorization": f"Bearer {self._config.github.api_key}",
            "Accept": "application/vnd.github+json",
        }

        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            users = await self._search(client, _GITHUB_SEARCH_USERS, github_handle or company_name)
            repos = await self._search(client, _GITHUB_SEARCH_REPOS, company_name)

        return {
            "source": "github",
            "users": users[:5],
            "repositories": repos[:10],
        }

    async def _search(
        self,
        client: httpx.AsyncClient,
        url: str,
        query: str,
    ) -> list[dict[str, Any]]:
        try:
            resp = await client.get(url, params={"q": query, "per_page": 10})
            resp.raise_for_status()
            return resp.json().get("items") or []
        except httpx.HTTPStatusError as exc:
            logger.warning("GitHubCollector: HTTP %s for %r", exc.response.status_code, query)
            return []
        except Exception as exc:
            logger.warning("GitHubCollector: error for %r — %s", query, exc)
            return []
