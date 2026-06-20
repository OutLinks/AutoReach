"""
Social presence checker.

Does lightweight head-checks to determine which social profiles exist for
a lead (Twitter/X, LinkedIn, GitHub, etc.) without full API credentials.
Also fetches recent public LinkedIn activity via ProxyCurl if available.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_SOCIAL_PATTERNS = {
    "twitter": "https://twitter.com/{handle}",
    "github": "https://github.com/{handle}",
}

_LINKEDIN_POSTS_URL = "https://nubela.co/proxycurl/api/v1/linkedin/person/posts"


class SocialChecker:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def check_profiles(
        self, twitter_handle: Optional[str], github_handle: Optional[str]
    ) -> dict[str, Optional[str]]:
        """Returns platform → URL if reachable, else None."""
        checks: dict[str, Optional[str]] = {}

        async with httpx.AsyncClient(timeout=8.0) as client:
            if twitter_handle:
                url = _SOCIAL_PATTERNS["twitter"].format(handle=twitter_handle.lstrip("@"))
                checks["twitter"] = await self._head_check(client, url)

            if github_handle:
                url = _SOCIAL_PATTERNS["github"].format(handle=github_handle.lstrip("@"))
                checks["github"] = await self._head_check(client, url)

        return checks

    async def fetch_recent_posts(self, linkedin_url: str) -> list[str]:
        """Fetches recent LinkedIn posts via ProxyCurl. Returns list of post texts."""
        if not self._config.proxycurl.is_ready() or not linkedin_url:
            return []

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    _LINKEDIN_POSTS_URL,
                    headers={"Authorization": f"Bearer {self._config.proxycurl.api_key}"},
                    params={"linkedin_profile_url": linkedin_url, "post_count": "10"},
                )

            if resp.status_code != 200:
                logger.warning("SocialChecker: HTTP %d fetching posts", resp.status_code)
                return []

            data = resp.json()
            posts = data.get("data") or []
            return [
                p.get("commentary", "") or p.get("text", "")
                for p in posts
                if p.get("commentary") or p.get("text")
            ][:10]

        except Exception as exc:
            logger.warning("SocialChecker: error fetching posts — %s", exc)
            return []

    @staticmethod
    async def _head_check(client: httpx.AsyncClient, url: str) -> Optional[str]:
        try:
            resp = await client.head(url, follow_redirects=True)
            return url if resp.status_code < 400 else None
        except Exception:
            return None
