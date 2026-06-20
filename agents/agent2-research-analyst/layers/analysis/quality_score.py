"""
Lead quality scorer for Agent 2.

Synthesizes all analysis results and scores the lead 1–10 with a grade.
This is the final analysis step before the output layer.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import (
    CompanyProfile,
    PainPoint,
    PersonalProfile,
    QualityScore,
)
from ...prompt import build_quality_score_prompt

logger = logging.getLogger(__name__)


class QualityScorer:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def score(
        self,
        lead_name: str,
        company_name: str,
        company_profile: Optional[CompanyProfile],
        pain_points: list[PainPoint],
        personal_profile: Optional[PersonalProfile],
        data_completeness: float,
    ) -> Optional[QualityScore]:
        company_json = company_profile.model_dump_json(indent=2) if company_profile else "{}"
        pain_json = json.dumps([p.model_dump() for p in pain_points], indent=2)
        personal_json = (
            personal_profile.model_dump_json(indent=2) if personal_profile else "{}"
        )

        system, user = build_quality_score_prompt(
            lead_name,
            company_name,
            company_json,
            pain_json,
            personal_json,
            data_completeness,
        )

        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            raw = response.content or ""
            return QualityScore(**self._parse(raw))
        except Exception as exc:
            logger.warning("QualityScorer: failed for %s — %s", lead_name, exc)
            return None

    @staticmethod
    def _parse(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(l for l in lines if not l.startswith("```"))
        return json.loads(text)
