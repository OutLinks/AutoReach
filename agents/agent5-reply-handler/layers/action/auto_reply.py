"""
Auto-reply generator (Action Layer #1).

Drafts the response body for intents the AI is cleared to handle (questions,
not-interested goodbyes, wrong-person referral asks, needs-time, confused). Uses
the LLM (prompt.build_reply_prompt) with per-intent guidance, and falls back to
deterministic templates so a reply is always produced.
"""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import (
    INTENT_CONFUSED,
    INTENT_NEEDS_TIME,
    INTENT_NOT_INTERESTED,
    INTENT_QUESTION,
    INTENT_WRONG_PERSON,
    IncomingReply,
)
from ...prompt import build_reply_prompt

logger = logging.getLogger(__name__)

_GUIDANCE: dict[str, str] = {
    INTENT_QUESTION: "Answer their question directly and briefly, then offer a quick call.",
    INTENT_NOT_INTERESTED: "Thank them, no pressure, leave the door open. Do NOT pitch again.",
    INTENT_WRONG_PERSON: "Politely ask who the right person to speak with is.",
    INTENT_NEEDS_TIME: "Acknowledge the timing, offer to follow up later, keep it warm.",
    INTENT_CONFUSED: "Clarify who you are and the one-line value proposition simply.",
}


class AutoReplyGenerator:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def generate(self, reply: IncomingReply, intent: str, include_link: bool) -> str:
        link = self._config.calendly_link if include_link else ""
        if self._config.use_llm_replies:
            body = await self._generate_llm(reply, intent, link)
            if body:
                return body
        return self._template(reply, intent, link)

    async def _generate_llm(self, reply: IncomingReply, intent: str, link: str) -> str:
        system, user = build_reply_prompt(
            reply,
            intent,
            sender_name=self._config.sender_name,
            calendly_link=link,
            guidance=_GUIDANCE.get(intent, ""),
            campaign_instruction=self._config.campaign_instruction,
        )
        try:
            adapter = get_model(self._config.model)
            response = await adapter.complete(
                self._config.model,
                [Message(role="system", content=system), Message(role="user", content=user)],
            )
            body = (response.content or "").strip()
            return body
        except Exception as exc:
            logger.warning("AutoReplyGenerator: LLM failed — %s", exc)
            return ""

    def _template(self, reply: IncomingReply, intent: str, link: str) -> str:
        name = reply.lead_first_name or "there"
        sender = self._config.sender_name
        company = reply.lead_company or "your company"

        if intent == INTENT_QUESTION:
            body = (
                f"Hi {name},\n\nGreat question — happy to walk you through it. "
                f"The short version: we help teams like {company} tackle exactly that.\n\n"
                f"Want to hop on a quick call?"
            )
        elif intent == INTENT_NOT_INTERESTED:
            body = (
                f"Hi {name},\n\nNo problem at all — I appreciate you letting me know. "
                f"If anything changes, feel free to reach out.\n\nBest of luck with {company}!"
            )
        elif intent == INTENT_WRONG_PERSON:
            body = (
                f"Hi {name},\n\nThanks for flagging that. Who would be the best person "
                f"on your team for me to reach out to?"
            )
        elif intent == INTENT_NEEDS_TIME:
            body = (
                f"Hi {name},\n\nTotally understand — timing is everything. I'll check "
                f"back in a little while. In the meantime, feel free to reach out anytime."
            )
        else:  # CONFUSED / default
            body = (
                f"Hi {name},\n\nApologies if that wasn't clear! In one line: we help "
                f"teams like {company} get more out of their outreach. Happy to explain more."
            )

        if link:
            body += f"\n\n{link}"
        return f"{body}\n\n{sender}"
