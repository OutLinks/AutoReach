"""Opening line (hook) writer."""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import InputContext
from ...prompt import build_hook_prompt

logger = logging.getLogger(__name__)


class HookWriter:
    def __init__(self, config: ServiceConfig) -> None:
        self._model = config.model
        self._adapter = get_model(config.model)

    async def write(self, ctx: InputContext, subject: str) -> str:
        system, user = build_hook_prompt(ctx, subject)
        try:
            response = await self._adapter.complete(
                self._model,
                [
                    Message(role="system", content=system),
                    Message(role="user", content=user),
                ],
            )
            hook = (response.content or "").strip()
            if not hook:
                raise ValueError("empty response")
            logger.debug("HookWriter: '%s'", hook[:80])
            return hook
        except Exception as exc:
            logger.warning("HookWriter: failed — %s", exc)
            # Fallback to the research-suggested hook
            return ctx.recommended_hook or (
                f"Noticed {ctx.lead_company} is doing interesting work in this space."
            )
