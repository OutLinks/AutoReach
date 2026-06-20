"""
Email angle strategist.

Synthesizes the company profile, pain points, and personal profile into
a specific outreach angle: hook, tone, CTA, and subject lines.

Depends on the other three analyzers having already run.
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
    EmailAngle,
    PainPoint,
    PersonalProfile,
)
from ...prompt import build_email_angle_prompt

logger = logging.getLogger(__name__)


class EmailAngleStrategist:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def strategize(
        self,
        lead_name: str,
        company_name: str,
        company_profile: Optional[CompanyProfile],
        pain_points: list[PainPoint],
        personal_profile: Optional[PersonalProfile],
    ) -> Optional[EmailAngle]:
        if not company_profile and not pain_points:
            logger.debug(
                "EmailAngleStrategist: insufficient analysis data for %s, skipping",
                lead_name,
            )
            return None

        company_json = company_profile.model_dump_json(indent=2) if company_profile else "{}"
        pain_json = json.dumps([p.model_dump() for p in pain_points], indent=2)
        personal_json = (
            personal_profile.model_dump_json(indent=2) if personal_profile else "{}"
        )

        system, user = build_email_angle_prompt(
            lead_name, company_name, company_json, pain_json, personal_json
        )

        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            raw = response.content or ""
            return EmailAngle(**self._parse(raw))
        except Exception as exc:
            logger.warning(
                "EmailAngleStrategist: failed for %s — %s", lead_name, exc
            )
            return None

    @staticmethod
    def _parse(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(l for l in lines if not l.startswith("```"))
        return json.loads(text)
