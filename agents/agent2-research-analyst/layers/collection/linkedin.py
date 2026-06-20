"""
ProxyCurl-based LinkedIn scraper for Agent 2.

Fetches both the person profile and their company page,
returning raw markdown text for each. Returns None on failure.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_PERSON_URL = "https://nubela.co/proxycurl/api/v2/linkedin/person"
_COMPANY_URL = "https://nubela.co/proxycurl/api/linkedin/company"


class LinkedInScraper:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def fetch_person(self, linkedin_url: str) -> Optional[str]:
        if not self._config.proxycurl.is_ready() or not linkedin_url:
            return None
        return await self._fetch(_PERSON_URL, {"url": linkedin_url})

    async def fetch_company(self, linkedin_company_url: str) -> Optional[str]:
        if not self._config.proxycurl.is_ready() or not linkedin_company_url:
            return None
        return await self._fetch(_COMPANY_URL, {"url": linkedin_company_url})

    async def _fetch(self, endpoint: str, params: dict) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {self._config.proxycurl.api_key}"},
                    params=params,
                )

            if resp.status_code == 404:
                logger.debug("LinkedInScraper: 404 for %s", params)
                return None
            if resp.status_code == 429:
                logger.warning("LinkedInScraper: rate limited by ProxyCurl")
                return None
            if resp.status_code != 200:
                logger.warning("LinkedInScraper: HTTP %d for %s", resp.status_code, params)
                return None

            data = resp.json()
            return self._to_markdown(data)

        except httpx.TimeoutException:
            logger.warning("LinkedInScraper: timeout for %s", params)
            return None
        except Exception as exc:
            logger.warning("LinkedInScraper: error — %s", exc)
            return None

    @staticmethod
    def _to_markdown(data: dict) -> str:
        """Converts the ProxyCurl JSON into a readable markdown string for the LLM."""
        lines: list[str] = []

        name = " ".join(filter(None, [data.get("first_name"), data.get("last_name")]))
        if name:
            lines.append(f"# {name}")

        for field in ("headline", "summary", "occupation", "country_full_name"):
            val = data.get(field)
            if val:
                lines.append(f"**{field}**: {val}")

        # Experience
        experiences = data.get("experiences") or []
        if experiences:
            lines.append("\n## Experience")
            for exp in experiences[:5]:
                title = exp.get("title", "")
                company = exp.get("company", "")
                date_range = f"{exp.get('starts_at', {}).get('year', '')}–{exp.get('ends_at', {}).get('year', 'present')}"
                desc = exp.get("description", "")
                lines.append(f"- **{title}** at {company} ({date_range})")
                if desc:
                    lines.append(f"  {desc[:300]}")

        # Education
        education = data.get("education") or []
        if education:
            lines.append("\n## Education")
            for edu in education[:3]:
                school = edu.get("school", "")
                degree = edu.get("degree_name", "")
                lines.append(f"- {degree} — {school}" if degree else f"- {school}")

        # Skills
        skills = [s.get("name", "") for s in (data.get("skills") or [])[:10] if s.get("name")]
        if skills:
            lines.append(f"\n**Skills**: {', '.join(skills)}")

        # Company-level fields
        for field in ("company_size", "company_type", "description", "specialities"):
            val = data.get(field)
            if val:
                lines.append(f"**{field}**: {val}")

        return "\n".join(lines)
