"""
ProductHunt search adapter.

Queries the ProductHunt GraphQL API to find startup makers matching the
search criteria. Returns leads with limited contact info — ProxyCurl/Clearbit
enrichment fills in emails and company data downstream.

API docs: https://api.producthunt.com/v2/docs
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import Lead, SearchCriteria

logger = logging.getLogger(__name__)

_GQL_URL = "https://api.producthunt.com/v2/api/graphql"

_POSTS_QUERY = """
query SearchPosts($first: Int!, $after: String, $topic: String) {
  posts(first: $first, after: $after, topic: $topic, order: RANKING) {
    edges {
      node {
        id
        name
        tagline
        description
        website
        createdAt
        votesCount
        topics {
          edges { node { name slug } }
        }
        makers {
          id
          name
          username
          twitterUsername
          profileImage
          headline
          websiteUrl
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        return re.sub(r"^www\.", "", host) or None
    except Exception:
        return None


class ProductHuntAdapter:
    """Finds startup makers on ProductHunt matching the given criteria."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, criteria: SearchCriteria, max_results: int) -> list[Lead]:
        """
        Page through ProductHunt posts and extract maker leads.
        Uses the first keyword/industry as the topic filter.
        """
        topic = self._pick_topic(criteria)
        leads: list[Lead] = []
        cursor: str | None = None
        seen_maker_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=30) as client:
            while len(leads) < max_results:
                variables: dict[str, Any] = {"first": 20}
                if topic:
                    variables["topic"] = topic
                if cursor:
                    variables["after"] = cursor

                try:
                    resp = await client.post(
                        _GQL_URL,
                        json={"query": _POSTS_QUERY, "variables": variables},
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    logger.error("ProductHunt HTTP %s: %s", exc.response.status_code, exc)
                    break
                except Exception as exc:
                    logger.error("ProductHunt request failed: %s", exc)
                    break

                errors = data.get("errors")
                if errors:
                    logger.error("ProductHunt GraphQL errors: %s", errors)
                    break

                posts_data = (data.get("data") or {}).get("posts") or {}
                edges = posts_data.get("edges") or []
                page_info = posts_data.get("pageInfo") or {}

                for edge in edges:
                    if len(leads) >= max_results:
                        break
                    post = edge.get("node") or {}
                    makers = post.get("makers") or []
                    for maker in makers:
                        if len(leads) >= max_results:
                            break
                        maker_id = maker.get("id", "")
                        if maker_id in seen_maker_ids:
                            continue
                        seen_maker_ids.add(maker_id)
                        lead = self._map_maker(maker, post)
                        if lead:
                            leads.append(lead)

                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")
                if not cursor:
                    break

        logger.info("ProductHunt found %d maker leads", len(leads))
        return leads

    def _pick_topic(self, criteria: SearchCriteria) -> str | None:
        """Map criteria to a ProductHunt topic slug."""
        # Simple keyword → topic mapping
        keyword_topic_map = {
            "saas": "saas",
            "ai": "artificial-intelligence",
            "developer": "developer-tools",
            "developer tools": "developer-tools",
            "productivity": "productivity",
            "marketing": "marketing",
            "design": "design-tools",
            "analytics": "analytics",
            "e-commerce": "e-commerce",
            "fintech": "fintech",
            "health": "health-and-fitness",
        }
        candidates = [k.lower() for k in criteria.keywords + criteria.industries]
        for candidate in candidates:
            for key, topic in keyword_topic_map.items():
                if key in candidate:
                    return topic
        return None

    def _map_maker(self, maker: dict[str, Any], post: dict[str, Any]) -> Lead | None:
        name = (maker.get("name") or "").strip()
        if not name:
            return None

        website = post.get("website") or maker.get("websiteUrl")
        twitter = maker.get("twitterUsername")
        topics = [
            e["node"]["name"]
            for e in (post.get("topics") or {}).get("edges", [])
        ]

        name_parts = name.split(" ", 1)
        first = name_parts[0] if name_parts else None
        last = name_parts[1] if len(name_parts) > 1 else None

        return Lead(
            first_name=first,
            last_name=last,
            full_name=name,
            email=None,        # PH doesn't expose emails; enrichment fills this
            title=maker.get("headline") or "Founder",
            twitter_url=f"https://twitter.com/{twitter}" if twitter else None,
            company_name=post.get("name"),
            company_website=website,
            company_domain=_extract_domain(website),
            company_description=post.get("tagline"),
            technologies=topics,
            sources=["producthunt"],
            raw_data={"producthunt": {"maker": maker, "post": post}},
            stage="raw",
        )
