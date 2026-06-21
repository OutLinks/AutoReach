"""
Snov.io search adapter.

Snov's prospect search is domain-scoped — it finds people *at a given company
domain*, optionally filtered by job title. It cannot discover companies from
scratch, so it runs as a second stage seeded with domains surfaced by the
discovery adapters (Apollo / ProductHunt / Hunter).

Flow:
  1. OAuth — exchange API User ID + Secret for a bearer access token.
  2. For each seed domain — search prospects (optionally by job title) →
     mapped to Lead objects.

API docs: https://snov.io/api
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.snov.io/v1/oauth/access_token"
_PROSPECTS_URL = "https://api.snov.io/v2/domain-search/prospects/start"

# Cap how many seed domains we drill into per run to stay within rate limits.
_MAX_DOMAINS = 10


class SnovAdapter:
    """Finds prospects at seed domains via Snov.io, filtered by job title."""

    def __init__(self, user_id: str, secret: str) -> None:
        self._user_id = user_id
        self._secret = secret

    async def search_by_domains(self, domains: list[str], criteria: SearchCriteria,
                                max_results: int) -> list[Lead]:
        if not domains:
            return []

        async with httpx.AsyncClient(timeout=30) as client:
            token = await self._get_token(client)
            if not token:
                return []

            headers = {"Authorization": f"Bearer {token}"}
            positions = (criteria.job_titles or [])[:10]   # Snov allows max 10
            leads: list[Lead] = []

            for domain in domains[:_MAX_DOMAINS]:
                if len(leads) >= max_results:
                    break
                prospects = await self._search_domain(client, headers, domain, positions)
                for prospect in prospects:
                    if len(leads) >= max_results:
                        break
                    leads.append(self._map_prospect(prospect, domain))

        logger.info("Snov found %d leads (from %d domains)", len(leads), len(domains))
        return leads

    async def _get_token(self, client: httpx.AsyncClient) -> str | None:
        try:
            resp = await client.post(_TOKEN_URL, data={
                "grant_type": "client_credentials",
                "client_id": self._user_id,
                "client_secret": self._secret,
            })
            resp.raise_for_status()
            return resp.json().get("access_token")
        except httpx.HTTPStatusError as exc:
            logger.error("Snov auth HTTP %s: %s", exc.response.status_code, exc)
        except Exception as exc:
            logger.error("Snov auth failed: %s", exc)
        return None

    async def _search_domain(self, client: httpx.AsyncClient, headers: dict[str, str],
                             domain: str, positions: list[str]) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"domain": domain}
        if positions:
            body["positions"] = positions

        try:
            resp = await client.post(_PROSPECTS_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Snov prospects HTTP %s (%s): %s",
                         exc.response.status_code, domain, exc)
            return []
        except Exception as exc:
            logger.error("Snov prospects failed (%s): %s", domain, exc)
            return []

        return data.get("data") or []

    def _map_prospect(self, prospect: dict[str, Any], domain: str) -> Lead:
        first = prospect.get("first_name")
        last = prospect.get("last_name")
        full = " ".join(p for p in (first, last) if p) or None
        source = prospect.get("source_page") or ""
        linkedin = source if "linkedin.com" in source else None

        return Lead(
            first_name=first,
            last_name=last,
            full_name=full,
            email=prospect.get("email"),   # usually None — enrichment resolves it
            title=prospect.get("position"),
            linkedin_url=linkedin,
            company_domain=domain,
            company_website=f"https://{domain}",
            sources=["snov"],
            raw_data={"snov": prospect},
            stage="raw",
        )
