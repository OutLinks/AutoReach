"""
Hunter Domain Search adapter.

Finds likely work emails for a company domain. This is discovery, not
deliverability verification; Abstract handles verification in Module 3.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"

_TITLE_PRIORITY = (
    "founder",
    "owner",
    "ceo",
    "president",
    "managing director",
    "vp",
    "director",
    "head",
    "manager",
)


class HunterDomainSearchAdapter:
    """Discovers a contact email for a company domain."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def enrich(self, lead: Lead) -> Lead:
        if lead.email or not lead.company_domain:
            return lead

        data = await self._fetch(lead.company_domain)
        if data is None:
            return lead

        emails = data.get("emails") or []
        selected = self._pick_email(emails)
        if selected:
            lead.email = selected.get("value")
            lead.first_name = lead.first_name or selected.get("first_name")
            lead.last_name = lead.last_name or selected.get("last_name")
            if not lead.full_name:
                lead.full_name = " ".join(
                    part for part in [lead.first_name, lead.last_name] if part
                ) or None
            lead.title = lead.title or selected.get("position")
            lead.department = lead.department or selected.get("department")
            confidence = selected.get("confidence")
            if confidence is not None:
                lead.email_score = float(confidence)

        lead.raw_data["hunter_domain_search"] = data
        lead.merge_source("hunter_domain_search")
        return lead

    async def _fetch(self, domain: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.get(
                    _DOMAIN_SEARCH_URL,
                    params={
                        "domain": domain,
                        "limit": 10,
                        "api_key": self._api_key,
                    },
                )
                resp.raise_for_status()
                return resp.json().get("data") or {}
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Hunter Domain Search rate limited")
                else:
                    logger.error("Hunter Domain Search HTTP %s for %s", exc.response.status_code, domain)
                return None
            except Exception as exc:
                logger.error("Hunter Domain Search failed for %s: %s", domain, exc)
                return None

    @staticmethod
    def _pick_email(emails: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not emails:
            return None

        def score(item: dict[str, Any]) -> tuple[int, float]:
            position = (item.get("position") or "").lower()
            title_rank = 0
            for idx, keyword in enumerate(_TITLE_PRIORITY):
                if keyword in position:
                    title_rank = len(_TITLE_PRIORITY) - idx
                    break
            return title_rank, float(item.get("confidence") or 0)

        return max(emails, key=score)
