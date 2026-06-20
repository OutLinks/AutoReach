"""
Company profile analyzer.

Calls the LLM with the raw website + LinkedIn + news data and extracts
a structured CompanyProfile. Returns None on failure.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import CompanyProfile, RawResearchData
from ...prompt import build_company_profile_prompt

logger = logging.getLogger(__name__)


class CompanyAnalyzer:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def analyze(
        self,
        lead_name: str,
        company_name: str,
        data: RawResearchData,
    ) -> Optional[CompanyProfile]:
        system, user = build_company_profile_prompt(lead_name, company_name, data)

        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            raw = response.content or ""
            return CompanyProfile(**self._parse(raw))
        except Exception as exc:
            logger.warning("CompanyAnalyzer: failed for %s — %s", company_name, exc)
            return None

    @staticmethod
    def _parse(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                l for l in lines if not l.startswith("```")
            )
        return json.loads(text)
