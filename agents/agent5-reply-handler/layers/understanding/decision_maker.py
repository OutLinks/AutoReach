"""
Decision maker (Understanding Layer #4).

Combines intent + sentiment + urgency + lead context into a single ActionDecision:
*what* to do, and *whether the AI is allowed to do it alone*.

Two stages:
  1. Map the intent to a default action.
  2. Apply the doc's escalation rules — any hit forces a human hand-off:
       - classification confidence < threshold,
       - high-value lead (score > 8),
       - negative sentiment / angry,
       - more than N exchanges already,
       - sensitive keyword (pricing, contract, legal, …).
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import (
    ACTION_AUTO_REPLY,
    ACTION_BOOK_MEETING,
    ACTION_HANDLE_OBJECTION,
    ACTION_HUMAN_HANDOFF,
    ACTION_NONE,
    ACTION_STOP,
    INTENT_ALREADY_CUSTOMER,
    INTENT_ANGRY,
    INTENT_CONFUSED,
    INTENT_INTERESTED,
    INTENT_MEETING_BOOKED,
    INTENT_NEEDS_TIME,
    INTENT_NOT_INTERESTED,
    INTENT_OBJECTION,
    INTENT_OUT_OF_OFFICE,
    INTENT_QUESTION,
    INTENT_UNCLEAR,
    INTENT_WRONG_PERSON,
    ActionDecision,
    IncomingReply,
    Understanding,
)

logger = logging.getLogger(__name__)

# Intent → (default action, may the AI auto-handle it by default?)
_INTENT_ACTION: dict[str, tuple[str, bool]] = {
    INTENT_INTERESTED: (ACTION_BOOK_MEETING, True),
    INTENT_QUESTION: (ACTION_AUTO_REPLY, True),
    INTENT_OBJECTION: (ACTION_HANDLE_OBJECTION, True),
    INTENT_NOT_INTERESTED: (ACTION_STOP, True),
    INTENT_WRONG_PERSON: (ACTION_AUTO_REPLY, True),
    INTENT_OUT_OF_OFFICE: (ACTION_NONE, True),
    INTENT_NEEDS_TIME: (ACTION_AUTO_REPLY, True),
    INTENT_CONFUSED: (ACTION_AUTO_REPLY, True),
    INTENT_MEETING_BOOKED: (ACTION_BOOK_MEETING, True),
    INTENT_ALREADY_CUSTOMER: (ACTION_HUMAN_HANDOFF, False),  # route to support
    INTENT_ANGRY: (ACTION_HUMAN_HANDOFF, False),
    INTENT_UNCLEAR: (ACTION_HUMAN_HANDOFF, False),
}


class DecisionMaker:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    def decide(self, reply: IncomingReply, u: Understanding) -> ActionDecision:
        intent = u.intent.intent
        action_type, auto_default = _INTENT_ACTION.get(
            intent, (ACTION_HUMAN_HANDOFF, False)
        )

        escalations = self._escalation_reasons(reply, u)
        auto_handle = auto_default and not escalations

        if not auto_handle and action_type not in (ACTION_NONE, ACTION_STOP):
            # Escalation (or an inherently human intent) → route to a person.
            final_action = ACTION_HUMAN_HANDOFF
        else:
            final_action = action_type

        decision = ActionDecision(
            action_type=final_action,
            auto_handle=auto_handle,
            confidence=u.intent.confidence,
            reason=(
                f"intent={intent} → {final_action}"
                + (" (escalated)" if escalations and final_action == ACTION_HUMAN_HANDOFF else "")
            ),
            escalation_reasons=escalations,
        )
        logger.info(
            "DecisionMaker: lead %s — %s (auto=%s, conf=%.2f)%s",
            reply.lead_id, final_action, auto_handle, u.intent.confidence,
            f" [{'; '.join(escalations)}]" if escalations else "",
        )
        return decision

    def _escalation_reasons(self, reply: IncomingReply, u: Understanding) -> list[str]:
        reasons: list[str] = []
        cfg = self._config

        if u.intent.confidence < cfg.min_confidence_to_auto_handle:
            reasons.append(
                f"low confidence ({u.intent.confidence:.0%} < {cfg.min_confidence_to_auto_handle:.0%})"
            )
        if reply.lead_score > cfg.high_value_lead_score:
            reasons.append(f"high-value lead (score {reply.lead_score:.1f})")
        if cfg.escalate_on_negative_sentiment and (
            u.sentiment.sentiment == "negative" or u.intent.intent == INTENT_ANGRY
        ):
            reasons.append("negative sentiment")
        if reply.prior_exchanges + 1 > cfg.max_auto_exchanges:
            reasons.append(f"too many exchanges (> {cfg.max_auto_exchanges})")

        text = (reply.clean_body or reply.raw_body).lower()
        hit = [kw for kw in cfg.escalate_keywords if kw in text]
        if hit:
            reasons.append(f"sensitive topic ({', '.join(hit)})")

        return reasons
