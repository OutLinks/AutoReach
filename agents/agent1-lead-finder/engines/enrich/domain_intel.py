"""WHOIS and DNS intelligence adapters."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_WHOISXML_URL = "https://www.whoisxmlapi.com/whoisserver/WhoisService"
_SECURITYTRAILS_BASE = "https://api.securitytrails.com/v1"


class WhoisXmlAdapter:
    """Adds domain registration intelligence."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def enrich(self, lead: Lead) -> Lead:
        if not lead.company_domain:
            return lead

        data = await self._lookup(lead.company_domain)
        if data is None:
            return lead

        record = data.get("WhoisRecord") or data
        created = record.get("createdDate") or record.get("createdDateNormalized")
        if created and not lead.founded_year:
            year = str(created)[:4]
            if year.isdigit():
                lead.founded_year = int(year)

        lead.raw_data["whoisxml"] = data
        lead.merge_source("whoisxml")
        return lead

    async def _lookup(self, domain: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.get(
                    _WHOISXML_URL,
                    params={
                        "apiKey": self._api_key,
                        "domainName": domain,
                        "outputFormat": "JSON",
                    },
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("WhoisXML rate limited")
                else:
                    logger.error("WhoisXML HTTP %s for %s", exc.response.status_code, domain)
                return None
            except Exception as exc:
                logger.error("WhoisXML request failed for %s: %s", domain, exc)
                return None


class SecurityTrailsAdapter:
    """Adds DNS/subdomain intelligence for technical enrichment."""

    def __init__(self, api_key: str) -> None:
        self._headers = {"APIKEY": api_key}

    async def enrich(self, lead: Lead) -> Lead:
        if not lead.company_domain:
            return lead

        data = await self._subdomains(lead.company_domain)
        if data is None:
            return lead

        subdomains = data.get("subdomains") or []
        if subdomains:
            lead.raw_data["securitytrails_subdomains"] = subdomains[:50]

        lead.raw_data["securitytrails"] = data
        lead.merge_source("securitytrails")
        return lead

    async def _subdomains(self, domain: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.get(
                    f"{_SECURITYTRAILS_BASE}/domain/{domain}/subdomains",
                    headers=self._headers,
                    params={"children_only": "false"},
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("SecurityTrails rate limited")
                else:
                    logger.error("SecurityTrails HTTP %s for %s", exc.response.status_code, domain)
                return None
            except Exception as exc:
                logger.error("SecurityTrails request failed for %s: %s", domain, exc)
                return None
