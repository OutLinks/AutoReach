"""
Apollo.io search adapter.

Calls the Apollo People Search endpoint to find leads matching the
SearchCriteria. Handles pagination and maps Apollo person objects → Lead.

API docs: https://apolloio.github.io/apollo-api-docs/?shell#people-search
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"

# Map Apollo employee count → standardised size bucket
_SIZE_BUCKETS = [
    (10, "1-10"),
    (50, "11-50"),
    (200, "51-200"),
    (500, "201-500"),
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


class ApolloAdapter:
    """Searches Apollo.io for people matching the given criteria."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, criteria: SearchCriteria, max_results: int) -> list[Lead]:
        """
        Paginate through Apollo search until `max_results` leads are collected.
        Returns an empty list on any unrecoverable error.
        """
        leads: list[Lead] = []
        page = 1
        per_page = min(25, max_results)

        async with httpx.AsyncClient(timeout=30) as client:
            while len(leads) < max_results:
                payload = self._build_payload(criteria, page, per_page)
                try:
                    resp = await client.post(
                        _SEARCH_URL,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    logger.error("Apollo HTTP %s: %s", exc.response.status_code, exc)
                    break
                except Exception as exc:
                    logger.error("Apollo request failed: %s", exc)
                    break

                people = data.get("people") or []
                if not people:
                    logger.debug("Apollo page %d returned no results — stopping", page)
                    break

                for person in people:
                    if len(leads) >= max_results:
                        break
                    lead = self._map_person(person)
                    leads.append(lead)

                pagination = data.get("pagination") or {}
                total = pagination.get("total_entries", 0)
                if len(leads) >= total:
                    break

                page += 1

        logger.info("Apollo found %d leads", len(leads))
        return leads

    def _build_payload(self, criteria: SearchCriteria, page: int,
                       per_page: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "page": page,
            "per_page": per_page,
        }

        if criteria.job_titles:
            payload["person_titles"] = criteria.job_titles

        if criteria.locations:
            # Apollo expects full location strings e.g. "San Francisco, California, United States"
            payload["person_locations"] = criteria.locations

        if criteria.industries:
            payload["organization_industry_tag_ids"] = []   # filled via org name filter
            payload["q_organization_industry_in"] = criteria.industries

        if criteria.keywords:
            payload["q_keywords"] = " ".join(criteria.keywords)

        # Always filter to leads with a known email
        payload["contact_email_status_in"] = ["verified", "guessed", "unverified"]

        return payload

    def _map_person(self, person: dict[str, Any]) -> Lead:
        org: dict[str, Any] = person.get("organization") or {}
        website = org.get("website_url")
        phone_numbers = person.get("phone_numbers") or []
        phone = phone_numbers[0].get("sanitized_number") if phone_numbers else None

        return Lead(
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            full_name=person.get("name"),
            email=person.get("email"),
            phone=phone,
            title=person.get("title"),
            seniority=person.get("seniority"),
            department=person.get("departments", [None])[0] if person.get("departments") else None,
            linkedin_url=person.get("linkedin_url"),
            city=person.get("city"),
            state=person.get("state"),
            country=person.get("country"),
            company_name=org.get("name"),
            company_website=website,
            company_domain=_extract_domain(website),
            company_linkedin_url=org.get("linkedin_url"),
            employee_count=org.get("estimated_num_employees"),
            company_size=_size_bucket(org.get("estimated_num_employees")),
            industry=org.get("industry") or (org.get("industries") or [None])[0],
            founded_year=org.get("founded_year"),
            sources=["apollo"],
            raw_data={"apollo": person},
            stage="raw",
        )
