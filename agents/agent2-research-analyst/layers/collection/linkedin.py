"""
PhantomBuster-based LinkedIn scraper for Agent 2.

Replaces the shut-down Proxycurl. PhantomBuster runs pre-configured "phantoms"
(scraper agents) in your dashboard; we trigger them by id over the API v2:

    1. launch    POST /agents/launch            → containerId
    2. poll      GET  /containers/fetch          → status (running → finished)
    3. result    GET  /containers/fetch-result-object → resultObject (JSON string)

Both fetch_person and fetch_company return raw markdown text for the LLM, or
None on any failure / missing configuration — the collection layer degrades
gracefully exactly as it did with Proxycurl.

PhantomBuster docs: https://hub.phantombuster.com/reference/
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_API_BASE = "https://api.phantombuster.com/api/v2"


class LinkedInScraper:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._pb = config.phantombuster

    async def fetch_person(self, linkedin_url: str) -> Optional[str]:
        if not self._pb.is_ready() or not linkedin_url:
            return None
        rows = await self._run_phantom(
            self._pb.person_agent_id,
            {"profileUrls": [linkedin_url], "numberOfProfiles": 1},
        )
        if not rows:
            return None
        return _person_to_markdown(rows[0])

    async def fetch_company(self, linkedin_company_url: str) -> Optional[str]:
        if not self._pb.company_ready() or not linkedin_company_url:
            return None
        rows = await self._run_phantom(
            self._pb.company_agent_id,
            {"companyUrls": [linkedin_company_url], "numberOfCompanies": 1},
        )
        if not rows:
            return None
        return _company_to_markdown(rows[0])

    # ── PhantomBuster API flow ───────────────────────────────────────────────

    async def _run_phantom(self, agent_id: str, argument: dict) -> list[dict]:
        """Launch a phantom, wait for it to finish, return its result rows."""
        headers = {
            "X-Phantombuster-Key": self._pb.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                container_id = await self._launch(client, headers, agent_id, argument)
                if not container_id:
                    return []
                if not await self._wait_for_finish(client, headers, container_id):
                    return []
                return await self._fetch_result(client, headers, container_id)
        except httpx.TimeoutException:
            logger.warning("LinkedInScraper: PhantomBuster request timed out (agent %s)", agent_id)
            return []
        except Exception as exc:
            logger.warning("LinkedInScraper: PhantomBuster error — %s", exc)
            return []

    async def _launch(
        self, client: httpx.AsyncClient, headers: dict, agent_id: str, argument: dict
    ) -> Optional[str]:
        resp = await client.post(
            f"{_API_BASE}/agents/launch",
            headers=headers,
            json={"id": agent_id, "argument": argument},
        )
        if resp.status_code != 200:
            logger.warning("LinkedInScraper: launch HTTP %d (agent %s)", resp.status_code, agent_id)
            return None
        return (resp.json() or {}).get("containerId")

    async def _wait_for_finish(
        self, client: httpx.AsyncClient, headers: dict, container_id: str
    ) -> bool:
        """Poll the container until it finishes or the max-wait budget is spent."""
        waited = 0
        while waited < self._pb.max_wait_seconds:
            resp = await client.get(
                f"{_API_BASE}/containers/fetch",
                headers=headers,
                params={"id": container_id},
            )
            if resp.status_code == 200:
                status = (resp.json() or {}).get("status")
                if status == "finished":
                    return True
            await asyncio.sleep(self._pb.poll_interval_seconds)
            waited += self._pb.poll_interval_seconds
        logger.warning("LinkedInScraper: container %s did not finish in time", container_id)
        return False

    async def _fetch_result(
        self, client: httpx.AsyncClient, headers: dict, container_id: str
    ) -> list[dict]:
        resp = await client.get(
            f"{_API_BASE}/containers/fetch-result-object",
            headers=headers,
            params={"id": container_id},
        )
        if resp.status_code != 200:
            logger.warning("LinkedInScraper: result HTTP %d (container %s)", resp.status_code, container_id)
            return []
        result_object = (resp.json() or {}).get("resultObject")
        return _parse_result_object(result_object)


# ── Result parsing + markdown rendering ──────────────────────────────────────

def _parse_result_object(result_object: Any) -> list[dict]:
    """resultObject is usually a JSON string holding a list of scraped rows."""
    if not result_object:
        return []
    if isinstance(result_object, list):
        return [r for r in result_object if isinstance(r, dict)]
    if isinstance(result_object, str):
        try:
            parsed = json.loads(result_object)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    if isinstance(result_object, dict):
        return [result_object]
    return []


def _first(row: dict, *keys: str) -> str:
    """Return the first non-empty value among the given keys (PB schemas vary)."""
    for key in keys:
        val = row.get(key)
        if val:
            return str(val)
    return ""


def _person_to_markdown(row: dict) -> str:
    """Render a PhantomBuster LinkedIn profile row into markdown for the LLM."""
    lines: list[str] = []

    name = _first(row, "fullName", "name") or " ".join(
        filter(None, [row.get("firstName"), row.get("lastName")])
    )
    if name:
        lines.append(f"# {name}")

    headline = _first(row, "headline", "occupation", "jobTitle")
    if headline:
        lines.append(f"**Headline**: {headline}")
    for label, keys in (
        ("Summary", ("summary", "description", "about")),
        ("Location", ("location", "locationName", "city")),
        ("Company", ("companyName", "company")),
    ):
        val = _first(row, *keys)
        if val:
            lines.append(f"**{label}**: {val}")

    jobs = row.get("jobs") or row.get("experiences") or row.get("experience") or []
    if isinstance(jobs, list) and jobs:
        lines.append("\n## Experience")
        for job in jobs[:5]:
            if not isinstance(job, dict):
                continue
            title = _first(job, "title", "jobTitle", "position")
            company = _first(job, "companyName", "company")
            when = _first(job, "dateRange", "duration", "period")
            entry = " at ".join(p for p in [title, company] if p) or "—"
            lines.append(f"- {entry}" + (f" ({when})" if when else ""))

    schools = row.get("schools") or row.get("education") or []
    if isinstance(schools, list) and schools:
        lines.append("\n## Education")
        for school in schools[:3]:
            if not isinstance(school, dict):
                continue
            name_ = _first(school, "schoolName", "school", "name")
            degree = _first(school, "degree", "degreeName")
            lines.append(f"- {degree} — {name_}" if degree else f"- {name_}")

    skills = row.get("skills") or []
    if isinstance(skills, list) and skills:
        names = [s.get("name", "") if isinstance(s, dict) else str(s) for s in skills[:10]]
        names = [n for n in names if n]
        if names:
            lines.append(f"\n**Skills**: {', '.join(names)}")

    return "\n".join(lines)


def _company_to_markdown(row: dict) -> str:
    """Render a PhantomBuster LinkedIn company row into markdown for the LLM."""
    lines: list[str] = []

    name = _first(row, "name", "companyName")
    if name:
        lines.append(f"# {name}")

    for label, keys in (
        ("Industry", ("industry", "industryName")),
        ("Company size", ("companySize", "employeeCount", "employeesRange")),
        ("Headquarters", ("headquarters", "location", "city")),
        ("Founded", ("founded", "foundedYear")),
        ("Specialties", ("specialties", "specialities")),
        ("Description", ("description", "about", "tagline")),
        ("Followers", ("followerCount", "followers")),
    ):
        val = _first(row, *keys)
        if val:
            lines.append(f"**{label}**: {val}")

    return "\n".join(lines)
