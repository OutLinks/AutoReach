"""
ProxyCurl enrichment adapter.

Enriches leads with deep LinkedIn data — both person profiles and company pages.

API docs: https://nubela.co/proxycurl/docs
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_PERSON_URL = "https://nubela.co/proxycurl/api/v2/linkedin/person"
_COMPANY_URL = "https://nubela.co/proxycurl/api/linkedin/company"

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


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        return re.sub(r"^www\.", "", host) or None
    except Exception:
        return None


class ProxyCurlAdapter:
    """Enriches leads with LinkedIn profile data via ProxyCurl."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def enrich_person(self, lead: Lead) -> Lead:
        """
        Fetch LinkedIn person profile and merge into lead.
        Requires `lead.linkedin_url`.
        """
        if not lead.linkedin_url:
            return lead

        data = await self._fetch(_PERSON_URL, {
            "linkedin_profile_url": lead.linkedin_url,
            "extra": "include",
            "personal_email": "include",
            "personal_contact_number": "include",
            "skills": "include",
            "use_cache": "if-present",
            "fallback_to_cache": "on-error",
        })

        if data is None:
            return lead

        # Merge only fields that are still empty (don't overwrite good data)
        lead.first_name = lead.first_name or data.get("first_name")
        lead.last_name = lead.last_name or data.get("last_name")
        lead.full_name = lead.full_name or data.get("full_name")
        lead.city = lead.city or data.get("city")
        lead.state = lead.state or data.get("state")
        lead.country = lead.country or data.get("country_full_name")

        # Personal email from ProxyCurl (may be better than Apollo's)
        personal_emails = data.get("personal_emails") or []
        if personal_emails and not lead.email:
            lead.email = personal_emails[0]

        experience = data.get("experiences") or []
        if experience and not lead.title:
            current = next((e for e in experience if not e.get("ends_at")), None)
            if current:
                lead.title = current.get("title")
                company = current.get("company") or ""
                lead.company_name = lead.company_name or company

        skills = [s.get("name") for s in (data.get("skills") or []) if s.get("name")]
        if skills:
            lead.technologies = list(set(lead.technologies + skills[:10]))

        # Update raw data
        lead.raw_data["proxycurl_person"] = data
        lead.merge_source("proxycurl")
        return lead

    async def enrich_company(self, lead: Lead) -> Lead:
        """
        Fetch LinkedIn company page and merge into lead.
        Requires `lead.company_linkedin_url`.
        """
        if not lead.company_linkedin_url:
            return lead

        data = await self._fetch(_COMPANY_URL, {
            "url": lead.company_linkedin_url,
            "use_cache": "if-present",
            "fallback_to_cache": "on-error",
        })

        if data is None:
            return lead

        lead.company_name = lead.company_name or data.get("name")
        lead.company_description = lead.company_description or data.get("description")
        lead.industry = lead.industry or data.get("industry")
        lead.founded_year = lead.founded_year or data.get("founded_year")

        count = data.get("company_size") or data.get("staff_count")
        if count and not lead.employee_count:
            try:
                lead.employee_count = int(count)
                lead.company_size = _size_bucket(lead.employee_count)
            except (TypeError, ValueError):
                pass

        website = data.get("website")
        lead.company_website = lead.company_website or website
        lead.company_domain = lead.company_domain or _extract_domain(website)

        specialities = data.get("specialities") or []
        if specialities:
            lead.technologies = list(set(lead.technologies + specialities[:10]))

        lead.raw_data["proxycurl_company"] = data
        lead.merge_source("proxycurl")
        return lead

    async def _fetch(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                resp = await client.get(url, params=params, headers=self._headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("ProxyCurl rate limited")
                else:
                    logger.error("ProxyCurl HTTP %s", exc.response.status_code)
                return None
            except Exception as exc:
                logger.error("ProxyCurl request failed: %s", exc)
                return None
