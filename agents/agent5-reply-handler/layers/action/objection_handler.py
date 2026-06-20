"""
Objection handler (Action Layer #3).

Classifies the objection and picks a reframing strategy (architecture doc table),
then drafts a reply that acknowledges the concern and reframes with a concrete
point — never badmouthing competitors. Sensitive objections (pricing/contract)
are filtered out upstream by the decision maker, so what reaches here is safe to
auto-handle.
"""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import IncomingReply
from ...prompt import build_objection_prompt

logger = logging.getLogger(__name__)

# objection signal → (label, strategy)
_OBJECTIONS: list[tuple[tuple[str, ...], str, str]] = [
    (("already have", "already use", "current"), "existing_solution",
     "Differentiate on a specific capability; do not badmouth their current tool."),
    (("too small", "tiny team", "just me"), "company_too_small",
     "Show results from similarly small teams."),
    (("no time", "too busy", "can't implement"), "no_time",
     "Offer a done-for-you / low-lift path."),
    (("think about", "not sure", "maybe later"), "need_to_think",
     "Share a quick proof point and create gentle urgency."),
    (("tried", "didn't work", "before"), "tried_similar",
     "Acknowledge the bad experience and differentiate clearly."),
]
_DEFAULT_STRATEGY = ("general", "Acknowledge the concern, reframe with one concrete benefit.")


class ObjectionHandler:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    def classify(self, reply: IncomingReply) -> tuple[str, str]:
        text = (reply.clean_body or reply.raw_body).lower()
        for signals, label, strategy in _OBJECTIONS:
            if any(s in text for s in signals):
                return label, strategy
        return _DEFAULT_STRATEGY

    async def handle(self, reply: IncomingReply) -> str:
        label, strategy = self.classify(reply)
        if self._config.use_llm_replies:
            body = await self._handle_llm(reply, label, strategy)
            if body:
                return body
        return self._template(reply, label)

    async def _handle_llm(self, reply: IncomingReply, label: str, strategy: str) -> str:
        system, user = build_objection_prompt(
            reply, label, strategy,
            sender_name=self._config.sender_name,
            calendly_link=self._config.calendly_link,
        )
        try:
            adapter = get_model(self._config.model)
            response = await adapter.complete(
                self._config.model,
                [Message(role="system", content=system), Message(role="user", content=user)],
            )
            return (response.content or "").strip()
        except Exception as exc:
            logger.warning("ObjectionHandler: LLM failed — %s", exc)
            return ""

    def _template(self, reply: IncomingReply, label: str) -> str:
        name = reply.lead_first_name or "there"
        return (
            f"Hi {name},\n\nTotally fair point. A lot of folks we work with felt the "
            f"same way at first — what changed their mind was seeing it on their own "
            f"numbers. Happy to show you a quick before/after if useful.\n\n"
            f"No pressure either way:\n{self._config.calendly_link}\n\n{self._config.sender_name}"
        )
