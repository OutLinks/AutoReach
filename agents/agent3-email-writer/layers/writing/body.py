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
        self._model = config.model
        self._adapter = get_model(config.model)

    async def compose(self, ctx: InputContext, subject: str, hook: str) -> str:
        system, user = build_body_prompt(ctx, subject, hook)
        try:
            response = await self._adapter.complete(
                self._model,
                [
                    Message(role="system", content=system),
                    Message(role="user", content=user),
                ],
            )
            body = (response.content or "").strip()
            if not body:
                raise ValueError("empty response")
            logger.debug("BodyComposer: %d chars", len(body))
            return body
        except Exception as exc:
            logger.warning("BodyComposer: failed — %s", exc)
            if ctx.campaign_instruction:
                return (
                    f"I'm {ctx.sender.full_name}. "
                    "I'm writing in response to the opportunity described in your "
                    "company's published information, based on the experience and "
                    "evidence specified in the campaign instructions."
                )
            return (
                f"I'm {ctx.sender.first_name} from {ctx.sender.company}. "
                f"We work with {ctx.lead_title.lower()}s to {ctx.brand_voice.value_proposition.lower()}."
            )
