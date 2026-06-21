"""
Hunter.io search adapter.

Two-step, criteria-driven lead discovery:
  1. Discover  — find companies matching the criteria (industry/keywords/tech/
     location/size) → returns domains. Free endpoint.
  2. Domain Search — for each discovered domain, pull email addresses with
     names and positions → mapped to Lead objects.

Auth: api_key via the X-API-KEY header.

API docs:
  - https://hunter.io/api-documentation/v2#discover
  - https://hunter.io/api-documentation/v2#domain-search
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_DISCOVER_URL = "https://api.hunter.io/v2/discover"
_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"

# How many companies to discover before drilling into their emails.
_MAX_DOMAINS = 10
# Emails to pull per domain.
_EMAILS_PER_DOMAIN = 10

# Hunter's Discover `headcount` filter only accepts a fixed set of buckets;
# anything else returns 400. Map/keep only values Hunter recognises.
_VALID_HEADCOUNT = {"1-10", "11-50", "51-200", "201-500", "1001-5000"}


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        return re.sub(r"^www\.", "", host) or None
    except Exception:
        return None


class HunterAdapter:
    """Discovers companies on Hunter, then pulls their emails as leads."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, criteria: SearchCriteria, max_results: int) -> list[Lead]:
        leads: list[Lead] = []
        async with httpx.AsyncClient(
            timeout=30, headers={"X-API-KEY": self._api_key},
        ) as client:
            domains = await self._discover(client, criteria)
            if not domains:
                logger.info("Hunter Discover returned no domains")
                return []

            # Spread leads across companies: take a balanced share from each
            # domain first, then top up from leftovers so one big company can't
            # crowd out the rest.
            per_domain = max(1, max_results // len(domains))
            leftovers: list[tuple[dict[str, Any], str, str | None]] = []

            for domain in domains:
                if len(leads) >= max_results:
                    break
                people = await self._domain_search(client, domain)
                for i, (person, org) in enumerate(people):
                    if i < per_domain and len(leads) < max_results:
                        leads.append(self._map_email(person, domain, org))
                    else:
                        leftovers.append((person, domain, org))

            for person, domain, org in leftovers:
                if len(leads) >= max_results:
                    break
                leads.append(self._map_email(person, domain, org))

        logger.info("Hunter found %d leads (from %d domains)", len(leads), len(domains))
        return leads

    async def _discover(self, client: httpx.AsyncClient,
                        criteria: SearchCriteria) -> list[str]:
        """Find company domains matching the criteria via Hunter Discover.

        Hunter's Discover filters are strict: `industry`, `technology` and
        `headquarters_location` only accept values from fixed vocabularies, and
        the `limit` param is rejected on lower plans. To stay robust against the
        free-text values the LLM produces, we route industries/keywords/tech
        into the free-text `keywords` filter and only pass `headcount` values
        Hunter recognises.
        """
        body: dict[str, Any] = {}

        keyword_terms = [
            *(criteria.keywords or []),
            *(criteria.industries or []),
            *(criteria.technologies or []),
        ]
        if keyword_terms:
            body["keywords"] = {"match": "any", "include": keyword_terms}

        headcount = [s for s in (criteria.company_sizes or []) if s in _VALID_HEADCOUNT]
        if headcount:
            body["headcount"] = headcount

        if not body:
            logger.debug("Hunter Discover: no usable filters in criteria — skipping")
            return []

        try:
            resp = await client.post(_DISCOVER_URL, json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Hunter Discover HTTP %s: %s", exc.response.status_code, exc)
            return []
        except Exception as exc:
            logger.error("Hunter Discover request failed: %s", exc)
            return []

        companies = data.get("data") or []
        domains = [c.get("domain") for c in companies if c.get("domain")]
        return domains[:_MAX_DOMAINS]

    async def _domain_search(self, client: httpx.AsyncClient,
                             domain: str) -> list[tuple[dict[str, Any], str | None]]:
        """Return (email_object, organization) pairs for a domain."""
        try:
            resp = await client.get(
                _DOMAIN_SEARCH_URL,
                params={"domain": domain, "limit": _EMAILS_PER_DOMAIN},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Hunter Domain Search HTTP %s (%s): %s",
                         exc.response.status_code, domain, exc)
            return []
        except Exception as exc:
            logger.error("Hunter Domain Search failed (%s): %s", domain, exc)
            return []

        payload = data.get("data") or {}
        org = payload.get("organization")
        emails = payload.get("emails") or []
        # Prefer personal (named) addresses over generic role inboxes.
        emails.sort(key=lambda e: 0 if e.get("type") == "personal" else 1)
        return [(e, org) for e in emails]

    def _map_email(self, email: dict[str, Any], domain: str,
                   org: str | None) -> Lead:
        first = email.get("first_name")
        last = email.get("last_name")
        full = " ".join(p for p in (first, last) if p) or None
        confidence = email.get("confidence")
        verification = (email.get("verification") or {}).get("status")

        return Lead(
            first_name=first,
            last_name=last,
            full_name=full,
            email=email.get("value"),
            title=email.get("position"),
            department=email.get("department"),
            seniority=email.get("seniority"),
            linkedin_url=email.get("linkedin"),
            twitter_url=(
                f"https://twitter.com/{email['twitter']}"
                if email.get("twitter") else None
            ),
            company_name=org,
            company_domain=domain,
            company_website=f"https://{domain}",
            email_status=verification,
            email_score=float(confidence) if confidence is not None else None,
            sources=["hunter"],
            raw_data={"hunter": email},
            stage="raw",
        )
