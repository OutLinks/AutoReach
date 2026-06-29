"""Wappalyzer technology detection adapter."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_LOOKUP_URL = "https://api.wappalyzer.com/v2/lookup/"


class WappalyzerAdapter:
    """Adds website technology signals to a lead."""

    def __init__(self, api_key: str) -> None:
        self._headers = {"x-api-key": api_key}

    async def enrich(self, lead: Lead) -> Lead:
        url = lead.company_website
        if not url:
            return lead

        data = await self._lookup(url)
        if data is None:
            return lead

        technologies = self._extract_technologies(data)
        if technologies:
            lead.technologies = sorted(set(lead.technologies + technologies[:25]))

        lead.raw_data["wappalyzer"] = data
        lead.merge_source("wappalyzer")
        return lead

    async def _lookup(self, url: str) -> list[dict[str, Any]] | dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                resp = await client.get(_LOOKUP_URL, params={"urls": url}, headers=self._headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Wappalyzer rate limited")
                else:
                    logger.error("Wappalyzer HTTP %s for %s", exc.response.status_code, url)
                return None
            except Exception as exc:
                logger.error("Wappalyzer request failed for %s: %s", url, exc)
                return None

    @staticmethod
    def _extract_technologies(data: list[dict[str, Any]] | dict[str, Any]) -> list[str]:
        records = data if isinstance(data, list) else [data]
        names: list[str] = []
        for record in records:
            tech_items = record.get("technologies") or record.get("applications") or []
            for item in tech_items:
                name = item.get("name") if isinstance(item, dict) else str(item)
                if name:
                    names.append(name)
        return names
