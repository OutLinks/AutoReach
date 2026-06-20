"""Subject line generator."""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import InputContext
from ...prompt import build_subject_prompt

logger = logging.getLogger(__name__)


class SubjectGenerator:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def generate(self, ctx: InputContext) -> str:
        system, user = build_subject_prompt(ctx)
        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            subject = (response.content or "").strip().strip('"')
            if not subject:
                raise ValueError("empty response")
            logger.debug("SubjectGenerator: '%s'", subject)
            return subject
        except Exception as exc:
            logger.warning("SubjectGenerator: failed — %s", exc)
            # Fallback: use first idea from research or a bare default
            return ctx.subject_line_ideas[0] if ctx.subject_line_ideas else (
                f"Quick thought about {ctx.lead_company}"
            )
