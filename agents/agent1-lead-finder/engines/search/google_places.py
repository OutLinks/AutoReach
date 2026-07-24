"""
Google Places Text Search adapter.

Finds local businesses from natural-language criteria and maps each place to a
company-level Lead. Contact emails are discovered later by Hunter Domain Search.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.types",
    "places.regularOpeningHours",
    "nextPageToken",
])


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
        return re.sub(r"^www\.", "", host) or None
    except Exception:
        return None


def _split_address(address: str | None) -> tuple[str | None, str | None, str | None]:
    if not address:
        return None, None, None

    parts = [part.strip() for part in address.split(",") if part.strip()]
    city = parts[-3] if len(parts) >= 3 else None
    state = parts[-2].split()[0] if len(parts) >= 2 and parts[-2].split() else None
    country = parts[-1] if parts else None
    return city, state, country


class GooglePlacesAdapter:
    """Searches Google Places for businesses matching the criteria."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, criteria: SearchCriteria, max_results: int) -> list[Lead]:
        leads: list[Lead] = []
        seen_place_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=30) as client:
            for query in self._queries(criteria):
                page_token: str | None = None
                while len(leads) < max_results:
                    batch, page_token = await self._search_page(client, query, page_token)
                    for place in batch:
                        place_id = place.get("id")
                        if place_id and place_id in seen_place_ids:
                            continue
                        if place_id:
                            seen_place_ids.add(place_id)
                        leads.append(self._map_place(place, criteria))
                        if len(leads) >= max_results:
                            break

                    if not page_token or len(leads) >= max_results:
                        break

        logger.info("Google Places found %d businesses", len(leads))
        return leads[:max_results]

    async def _search_page(
        self,
        client: httpx.AsyncClient,
        query: str,
        page_token: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        payload: dict[str, Any] = {
            "textQuery": query,
            "pageSize": 20,
        }
        if page_token:
            payload["pageToken"] = page_token

        try:
            resp = await client.post(
                _SEARCH_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("places") or [], data.get("nextPageToken")
        except httpx.HTTPStatusError as exc:
            logger.error("Google Places HTTP %s for query %r", exc.response.status_code, query)
            return [], None
        except Exception as exc:
            logger.error("Google Places request failed for query %r: %s", query, exc)
            return [], None

    def _queries(self, criteria: SearchCriteria) -> list[str]:
        business_terms = criteria.keywords or criteria.industries or ["businesses"]
        locations = criteria.locations or [""]

        queries: list[str] = []
        for term in business_terms[:3]:
            for location in locations[:3]:
                query = f"{term} in {location}".strip()
                if query not in queries:
                    queries.append(query)
        return queries

    def _map_place(self, place: dict[str, Any], criteria: SearchCriteria) -> Lead:
        name = (place.get("displayName") or {}).get("text")
        website = place.get("websiteUri")
        address = place.get("formattedAddress")
        city, state, country = _split_address(address)
        phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber")
        place_types = place.get("types") or []

        return Lead(
            company_name=name,
            company_website=website,
            company_domain=_extract_domain(website),
            phone=phone,
            city=city,
            state=state,
            country=country,
            industry=(criteria.industries or place_types or [None])[0],
            sources=["google_places"],
            raw_data={"google_places": place},
            stage="raw",
        )
