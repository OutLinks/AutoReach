"""Crunchbase company intelligence adapter."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.crunchbase.com/api/v4/searches/organizations"


class CrunchbaseAdapter:
    """Adds startup/company intelligence when Crunchbase has a match."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def enrich(self, lead: Lead) -> Lead:
        query = lead.company_name or lead.company_domain
        if not query:
            return lead

        data = await self._search(query)
        if data is None:
            return lead

        organization = self._first_entity(data)
        if organization:
            props = organization.get("properties") or {}
            lead.company_description = lead.company_description or props.get("short_description")
            lead.founded_year = lead.founded_year or self._year(props.get("founded_on"))
            lead.funding_total = lead.funding_total or self._money(props.get("funding_total"))
            lead.funding_stage = lead.funding_stage or props.get("last_funding_type")
            categories = props.get("categories") or []
            if categories and not lead.industry:
                lead.industry = categories[0].get("value") if isinstance(categories[0], dict) else str(categories[0])

        lead.raw_data["crunchbase"] = data
        lead.merge_source("crunchbase")
        return lead

    async def _search(self, query: str) -> dict[str, Any] | None:
        payload = {
            "field_ids": [
                "identifier",
                "short_description",
                "founded_on",
                "funding_total",
                "last_funding_type",
                "categories",
            ],
            "query": [
                {
                    "type": "predicate",
                    "field_id": "identifier",
                    "operator_id": "contains",
                    "values": [query],
                }
            ],
            "limit": 1,
        }

        async with httpx.AsyncClient(timeout=25) as client:
            try:
                resp = await client.post(
                    _SEARCH_URL,
                    params={"user_key": self._api_key},
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Crunchbase rate limited")
                else:
                    logger.error("Crunchbase HTTP %s for %s", exc.response.status_code, query)
                return None
            except Exception as exc:
                logger.error("Crunchbase request failed for %s: %s", query, exc)
                return None

    @staticmethod
    def _first_entity(data: dict[str, Any]) -> dict[str, Any] | None:
        entities = data.get("entities") or []
        return entities[0] if entities else None

    @staticmethod
    def _year(value: Any) -> int | None:
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
        if isinstance(value, dict):
            year = value.get("value", "")[:4]
            return int(year) if year.isdigit() else None
        return None

    @staticmethod
    def _money(value: Any) -> str | None:
        if isinstance(value, dict):
            amount = value.get("value_usd") or value.get("value")
            return str(amount) if amount is not None else None
        return str(value) if value is not None else None
