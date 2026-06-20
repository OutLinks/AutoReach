"""
Pain point extractor.

Prompts the LLM to find specific, evidence-backed business pain points
from the raw research data. Returns a list of PainPoint objects.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import PainPoint, RawResearchData
from ...prompt import build_pain_points_prompt

logger = logging.getLogger(__name__)


class PainPointExtractor:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def extract(
        self,
        lead_name: str,
        company_name: str,
        data: RawResearchData,
    ) -> list[PainPoint]:
        system, user = build_pain_points_prompt(lead_name, company_name, data)

        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            raw = response.content or "[]"
            items = self._parse(raw)
            return [PainPoint(**item) for item in items if isinstance(item, dict)]
        except Exception as exc:
            logger.warning("PainPointExtractor: failed for %s — %s", company_name, exc)
            return []

    @staticmethod
    def _parse(text: str) -> list:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(l for l in lines if not l.startswith("```"))
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
