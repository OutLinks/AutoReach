"""
Personal profile builder.

Uses LinkedIn data and social activity to build a profile of the
individual decision-maker — not the company.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import PersonalProfile, RawResearchData
from ...prompt import build_personal_profile_prompt

logger = logging.getLogger(__name__)


class PersonalProfileBuilder:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def build(
        self,
        lead_name: str,
        title: str,
        company_name: str,
        data: RawResearchData,
    ) -> Optional[PersonalProfile]:
        if not data.linkedin_person and not data.social_posts and not any(
            data.social_profiles.values()
        ):
            logger.debug(
                "PersonalProfileBuilder: no personal data for %s, skipping", lead_name
            )
            return None

        system, user = build_personal_profile_prompt(lead_name, title, company_name, data)

        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            raw = response.content or ""
            return PersonalProfile(**self._parse(raw))
        except Exception as exc:
            logger.warning("PersonalProfileBuilder: failed for %s — %s", lead_name, exc)
            return None

    @staticmethod
    def _parse(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(l for l in lines if not l.startswith("```"))
        return json.loads(text)
