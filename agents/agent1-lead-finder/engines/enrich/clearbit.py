"""
Clearbit enrichment adapter.

Enriches leads with company and person intelligence from a domain or email.

API docs: https://dashboard.clearbit.com/docs
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_COMPANY_URL = "https://company.clearbit.com/v2/companies/find"
_PERSON_URL = "https://person.clearbit.com/v2/people/find"

_SIZE_BUCKETS = [
    (10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"),
    (float("inf"), "500+"),
]


def _size_bucket(count: int | None) -> str | None:
    if count is None:
        return None
    for ceiling, label in _SIZE_BUCKETS:
        if count <= ceiling:
            return label
    return "500+"


class ClearbitAdapter:
    """Enriches leads with Clearbit company and person data."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._auth = (api_key, "")

    async def enrich_company(self, lead: Lead) -> Lead:
        """
        Lookup company by domain. Requires `lead.company_domain`.
        """
        if not lead.company_domain:
            return lead

        data = await self._fetch(_COMPANY_URL, {"domain": lead.company_domain})
        if data is None:
            return lead

        lead.company_name = lead.company_name or data.get("name")
        lead.company_description = lead.company_description or data.get("description")
        lead.industry = lead.industry or (data.get("category") or {}).get("industry")
        lead.founded_year = lead.founded_year or data.get("foundedYear")
        lead.annual_revenue = lead.annual_revenue or (
            str(data["metrics"]["annualRevenue"])
            if (data.get("metrics") or {}).get("annualRevenue")
            else None
        )

        count = (data.get("metrics") or {}).get("employees")
        if count and not lead.employee_count:
            lead.employee_count = count
            lead.company_size = _size_bucket(count)

        # Tech stack
        tech_list = data.get("tech") or []
        if tech_list:
            lead.technologies = list(set(lead.technologies + tech_list[:15]))

        # Social
        social = data.get("linkedin") or {}
        if social.get("handle") and not lead.company_linkedin_url:
            lead.company_linkedin_url = f"https://linkedin.com/company/{social['handle']}"

        # Funding
        funding = data.get("crunchbase") or {}
        if funding.get("handle"):
            lead.raw_data["crunchbase_handle"] = funding["handle"]

        lead.raw_data["clearbit_company"] = data
        lead.merge_source("clearbit")
        return lead

    async def enrich_person(self, lead: Lead) -> Lead:
        """
        Lookup person by email. Requires a valid `lead.email`.
        """
        if not lead.email or lead.email_status == "invalid":
            return lead

        data = await self._fetch(_PERSON_URL, {"email": lead.email})
        if data is None:
            return lead

        lead.first_name = lead.first_name or data.get("name", {}).get("givenName")
        lead.last_name = lead.last_name or data.get("name", {}).get("familyName")
        lead.full_name = lead.full_name or data.get("name", {}).get("fullName")

        # LinkedIn from Clearbit person
        linkedin_handle = (data.get("linkedin") or {}).get("handle")
        if linkedin_handle and not lead.linkedin_url:
            lead.linkedin_url = f"https://linkedin.com/in/{linkedin_handle}"

        employment = data.get("employment") or {}
        lead.title = lead.title or employment.get("title")

        lead.raw_data["clearbit_person"] = data
        lead.merge_source("clearbit")
        return lead

    async def _fetch(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.get(url, params=params, auth=self._auth)
                if resp.status_code in (404, 422):
                    return None
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Clearbit rate limited")
                elif exc.response.status_code != 404:
                    logger.error("Clearbit HTTP %s", exc.response.status_code)
                return None
            except Exception as exc:
                logger.error("Clearbit request failed: %s", exc)
                return None
