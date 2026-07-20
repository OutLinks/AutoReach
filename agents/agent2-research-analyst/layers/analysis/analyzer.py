"""
AI Analysis Layer orchestrator.

Runs all five analysis tasks for a single lead.
Tasks 1–3 (company, pain points, personal) run in parallel.
Tasks 4–5 (email angle, quality score) run after 1–3 complete since they
depend on the earlier results.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...config import ServiceConfig
from ...models import (
    CompanyProfile,
    EmailAngle,
    PainPoint,
    PersonalProfile,
    QualityScore,
    RawResearchData,
    ResearchProfile,
)
from .company import CompanyAnalyzer
from .pain_points import PainPointExtractor
from .personal import PersonalProfileBuilder
from .email_angle import EmailAngleStrategist
from .quality_score import QualityScorer

logger = logging.getLogger(__name__)


class AnalysisLayer:
    def __init__(self, config: ServiceConfig) -> None:
        self._campaign_instruction = config.campaign_instruction
        self._company = CompanyAnalyzer(config)
        self._pain_points = PainPointExtractor(config)
        self._personal = PersonalProfileBuilder(config)
        self._email_angle = EmailAngleStrategist(config)
        self._quality = QualityScorer(config)

    async def analyze(self, lead: dict, data: RawResearchData, profile: ResearchProfile) -> None:
        """
        Runs all analyses and writes results directly into `profile`.
        Does not raise — partial results are better than no results.
        """
        lead_name = _lead_name(lead)
        company_name = lead.get("company_name", "Unknown")
        title = lead.get("title", "")

        # Phase 1: independent analyses — run concurrently
        company_result, pain_points_result, personal_result = await asyncio.gather(
            self._company.analyze(lead_name, company_name, data),
            self._pain_points.extract(lead_name, company_name, data),
            self._personal.build(lead_name, title, company_name, data),
            return_exceptions=True,
        )

        company_profile = company_result if isinstance(company_result, CompanyProfile) else None
        pain_points = pain_points_result if isinstance(pain_points_result, list) else []
        personal_profile = personal_result if isinstance(personal_result, PersonalProfile) else None

        if isinstance(company_result, Exception):
            logger.warning("AnalysisLayer: company analysis error — %s", company_result)
        if isinstance(pain_points_result, Exception):
            logger.warning("AnalysisLayer: pain point error — %s", pain_points_result)
        if isinstance(personal_result, Exception):
            logger.warning("AnalysisLayer: personal profile error — %s", personal_result)

        # Phase 2: dependent analyses — run concurrently but after phase 1
        email_result, quality_result = await asyncio.gather(
            self._email_angle.strategize(
                lead_name, company_name, company_profile, pain_points, personal_profile,
                self._campaign_instruction,
            ),
            self._quality.score(
                lead_name,
                company_name,
                company_profile,
                pain_points,
                personal_profile,
                data.completeness,
                self._campaign_instruction,
            ),
            return_exceptions=True,
        )

        email_angle = email_result if isinstance(email_result, EmailAngle) else None
        quality_score = quality_result if isinstance(quality_result, QualityScore) else None

        # Write everything into the profile
        profile.company_profile = company_profile
        profile.pain_points = pain_points
        profile.personal_profile = personal_profile
        profile.email_angle = email_angle
        profile.quality_score = quality_score

        logger.info(
            "AnalysisLayer: %s — company=%s pain_points=%d personal=%s angle=%s score=%s",
            company_name,
            "✓" if company_profile else "✗",
            len(pain_points),
            "✓" if personal_profile else "✗",
            "✓" if email_angle else "✗",
            f"{quality_score.score:.1f}" if quality_score else "✗",
        )


def _lead_name(lead: dict) -> str:
    first = lead.get("first_name", "")
    last = lead.get("last_name", "")
    full = lead.get("full_name", "")
    if full:
        return full
    return " ".join(filter(None, [first, last])) or "Unknown"
