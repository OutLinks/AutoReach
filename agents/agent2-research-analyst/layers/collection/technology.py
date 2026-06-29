"""Wappalyzer technology collector for Agent 2 research."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...config import ServiceConfig

logger = logging.getLogger(__name__)

_LOOKUP_URL = "https://api.wappalyzer.com/v2/lookup/"


class TechnologyCollector:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def detect(self, website_url: str) -> dict[str, Any]:
        if not website_url or not self._config.wappalyzer.is_ready():
            return {}

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.get(
                    _LOOKUP_URL,
                    params={"urls": website_url},
                    headers={"x-api-key": self._config.wappalyzer.api_key},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("TechnologyCollector: Wappalyzer HTTP %s", exc.response.status_code)
            return {}
        except Exception as exc:
            logger.warning("TechnologyCollector: Wappalyzer error — %s", exc)
            return {}

        return {
            "source": "wappalyzer",
            "technologies": self._extract_names(data),
            "raw": data,
        }

    @staticmethod
    def _extract_names(data: Any) -> list[str]:
        records = data if isinstance(data, list) else [data]
        names: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            for item in record.get("technologies") or record.get("applications") or []:
                name = item.get("name") if isinstance(item, dict) else str(item)
                if name:
                    names.append(name)
        return sorted(set(names))
