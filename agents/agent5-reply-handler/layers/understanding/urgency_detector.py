"""
Urgency detector (Understanding Layer #3).

Decides how fast a human (or the agent) should respond. Urgency is a function of
intent, sentiment, lead value, and explicit time cues in the text. High urgency
short-circuits batching — the agent surfaces these first.
"""

from __future__ import annotations

from ...config import ServiceConfig
from ...models import (
    INTENT_ANGRY,
    INTENT_INTERESTED,
    INTENT_MEETING_BOOKED,
    IncomingReply,
    IntentResult,
    SentimentResult,
    UrgencyResult,
)

_TIME_CUES = ("asap", "urgent", "today", "right away", "immediately", "this morning", "by eod")


class UrgencyDetector:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    def detect(
        self,
        reply: IncomingReply,
        intent: IntentResult,
        sentiment: SentimentResult,
    ) -> UrgencyResult:
        reasons: list[str] = []
        score = 0

        if intent.intent in (INTENT_INTERESTED, INTENT_MEETING_BOOKED):
            score += 2
            reasons.append("hot intent — respond fast to keep momentum")
        if intent.intent == INTENT_ANGRY or sentiment.sentiment == "negative":
            score += 2
            reasons.append("negative sentiment — needs prompt, careful handling")
        if reply.lead_score >= self._config.high_value_lead_score:
            score += 1
            reasons.append("high-value lead")

        text = (reply.clean_body or reply.raw_body).lower()
        if any(cue in text for cue in _TIME_CUES):
            score += 2
            reasons.append("explicit time pressure in the message")

        level = "high" if score >= 3 else "medium" if score >= 1 else "low"
        return UrgencyResult(level=level, reasons=reasons)
