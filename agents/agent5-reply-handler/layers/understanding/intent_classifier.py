"""
Intent classifier (Understanding Layer #1).

Classifies a reply into one of the 12 intent categories. Prefers the LLM (strict
JSON via prompt.build_intent_prompt) and falls back to a keyword heuristic when
the LLM is unavailable or returns garbage — so classification always produces a
result (with lower confidence on the fallback path).
"""

from __future__ import annotations

import json
import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import (
    INTENT_ALREADY_CUSTOMER,
    INTENT_ANGRY,
    INTENT_INTERESTED,
    INTENT_MEETING_BOOKED,
    INTENT_NEEDS_TIME,
    INTENT_NOT_INTERESTED,
    INTENT_OBJECTION,
    INTENT_OUT_OF_OFFICE,
    INTENT_QUESTION,
    INTENT_UNCLEAR,
    INTENT_WRONG_PERSON,
    ALL_INTENTS,
    IncomingReply,
    IntentResult,
)
from ...prompt import build_intent_prompt

logger = logging.getLogger(__name__)

# Ordered keyword rules for the offline fallback (first match wins). Negative /
# more-specific intents are checked before INTERESTED, since phrases like
# "not interested" contain the substring "interested".
_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    (INTENT_OUT_OF_OFFICE, ("out of office", "on vacation", "away from my desk", "annual leave")),
    (INTENT_MEETING_BOOKED, ("booked", "see you then", "calendar invite", "confirmed for")),
    (INTENT_ANGRY, ("stop emailing", "harass", "report you", "unsubscribe", "leave me alone", "spam")),
    (INTENT_NOT_INTERESTED, ("not interested", "no thanks", "no thank you", "not a fit", "please remove", "pass")),
    (INTENT_WRONG_PERSON, ("wrong person", "not the right", "reach out to", "no longer with", "speak to")),
    (INTENT_ALREADY_CUSTOMER, ("already use", "already a customer", "we use", "current customer")),
    (INTENT_NEEDS_TIME, ("not right now", "circle back", "next quarter", "later", "busy right now")),
    (INTENT_OBJECTION, ("too expensive", "no budget", "already have", "not sure", "concern", "price")),
    (INTENT_INTERESTED, ("interested", "tell me more", "sounds good", "let's talk", "send me", "yes")),
    (INTENT_QUESTION, ("how", "what", "when", "does it", "can you", "?")),
]


class IntentClassifier:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def classify(self, reply: IncomingReply) -> IntentResult:
        if self._config.use_llm_replies:
            result = await self._classify_llm(reply)
            if result is not None:
                return result
        return self._classify_keywords(reply)

    async def _classify_llm(self, reply: IncomingReply) -> IntentResult | None:
        system, user = build_intent_prompt(reply, self._config.campaign_instruction)
        try:
            adapter = get_model(self._config.model)
            response = await adapter.complete(
                self._config.model,
                [Message(role="system", content=system), Message(role="user", content=user)],
            )
            raw = (response.content or "").strip()
            if raw.startswith("```"):
                raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```"))
            data = json.loads(raw)
            intent = data.get("intent", INTENT_UNCLEAR)
            if intent not in ALL_INTENTS:
                intent = INTENT_UNCLEAR
            return IntentResult(
                intent=intent,
                confidence=float(data.get("confidence", 0.0)),
                reasoning=data.get("reasoning", ""),
            )
        except Exception as exc:
            logger.warning("IntentClassifier: LLM classify failed — %s", exc)
            return None

    def _classify_keywords(self, reply: IncomingReply) -> IntentResult:
        text = (reply.clean_body or reply.raw_body).lower()
        for intent, keywords in _KEYWORD_RULES:
            if any(k in text for k in keywords):
                return IntentResult(
                    intent=intent,
                    confidence=0.55,  # heuristic → deliberately below auto-handle bar
                    reasoning=f"keyword match for {intent}",
                )
        return IntentResult(intent=INTENT_UNCLEAR, confidence=0.3, reasoning="no keyword match")
