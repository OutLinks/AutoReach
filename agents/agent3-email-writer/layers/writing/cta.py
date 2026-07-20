"""Call-to-action writer."""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import InputContext
from ...prompt import build_cta_prompt

logger = logging.getLogger(__name__)


class CTAWriter:
    def __init__(self, config: ServiceConfig) -> None:
        self._model = config.model
        self._adapter = get_model(config.model)

    async def write(self, ctx: InputContext, body: str) -> str:
        system, user = build_cta_prompt(ctx, body)
        try:
            response = await self._adapter.complete(
                self._model,
                [
                    Message(role="system", content=system),
                    Message(role="user", content=user),
                ],
            )
            cta = (response.content or "").strip()
            if not cta:
                raise ValueError("empty response")
            logger.debug("CTAWriter: '%s'", cta[:80])
            return cta
        except Exception as exc:
            logger.warning("CTAWriter: failed — %s", exc)
            return ctx.recommended_cta or "Would it make sense to chat for 15 minutes?"
