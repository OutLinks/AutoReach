"""
Social presence checker.

Does lightweight head-checks to determine which social profiles exist for
a lead (Twitter/X, GitHub, etc.) without full API credentials.
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

    @staticmethod
    async def _head_check(client: httpx.AsyncClient, url: str) -> Optional[str]:
        try:
            resp = await client.head(url, follow_redirects=True)
            return url if resp.status_code < 400 else None
        except Exception:
            return None
