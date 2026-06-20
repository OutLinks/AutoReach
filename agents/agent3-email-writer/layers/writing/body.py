"""Email body composer."""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import InputContext
from ...prompt import build_body_prompt

logger = logging.getLogger(__name__)


class BodyComposer:
    def __init__(self, config: ServiceConfig) -> None:
        self._adapter = get_model(config.model)

    async def compose(self, ctx: InputContext, subject: str, hook: str) -> str:
        system, user = build_body_prompt(ctx, subject, hook)
        try:
            response = await self._adapter.complete(
                messages=[Message(role="user", content=user)],
                system=system,
            )
            body = (response.content or "").strip()
            if not body:
                raise ValueError("empty response")
            logger.debug("BodyComposer: %d chars", len(body))
            return body
        except Exception as exc:
            logger.warning("BodyComposer: failed — %s", exc)
            # Minimal fallback
            return (
                f"I'm {ctx.sender.first_name} from {ctx.sender.company}. "
                f"We work with {ctx.lead_title.lower()}s to {ctx.brand_voice.value_proposition.lower()}."
            )
