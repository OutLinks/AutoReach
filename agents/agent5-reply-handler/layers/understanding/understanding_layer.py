"""
Understanding Layer orchestrator.

Runs the four understanding components for one reply and returns the bundled
Understanding plus the ActionDecision:

  intent_classifier (LLM) → sentiment_analyzer → urgency_detector → decision_maker

Sentiment and urgency are rule-based and instant; only intent needs the model, so
the layer is cheap and mostly deterministic.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import ActionDecision, IncomingReply, Understanding
from .intent_classifier import IntentClassifier
from .sentiment_analyzer import analyze as analyze_sentiment
from .urgency_detector import UrgencyDetector
from .decision_maker import DecisionMaker

logger = logging.getLogger(__name__)


class UnderstandingLayer:
    def __init__(self, config: ServiceConfig) -> None:
        self._intent = IntentClassifier(config)
        self._urgency = UrgencyDetector(config)
        self._decision = DecisionMaker(config)

    async def analyze(self, reply: IncomingReply) -> tuple[Understanding, ActionDecision]:
        intent = await self._intent.classify(reply)
        sentiment = analyze_sentiment(reply)
        urgency = self._urgency.detect(reply, intent, sentiment)

        understanding = Understanding(intent=intent, sentiment=sentiment, urgency=urgency)
        decision = self._decision.decide(reply, understanding)
        return understanding, decision
