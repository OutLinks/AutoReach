"""
Human handoff (Action Layer #4).

When the decision maker escalates, this builds the package a human needs to take
over fast: a one-glance summary, why it escalated, the urgency, an excerpt of the
conversation, and an optional suggested response the human can approve or edit.

The handoff does NOT send anything to the lead — it routes to a person. The
output layer persists it and fires a notification.
"""

from __future__ import annotations

import logging

from ...models import (
    ActionDecision,
    HandoffPackage,
    IncomingReply,
    Understanding,
)

logger = logging.getLogger(__name__)


class HumanHandoff:
    def build(
        self,
        reply: IncomingReply,
        understanding: Understanding,
        decision: ActionDecision,
        conversation_excerpt: str = "",
        suggested_response: str = "",
    ) -> HandoffPackage:
        reason = "; ".join(decision.escalation_reasons) or decision.reason
        summary = (
            f"{reply.lead_first_name or 'Lead'} at {reply.lead_company or 'unknown company'} "
            f"replied with intent {understanding.intent.intent} "
            f"({understanding.sentiment.sentiment} sentiment, "
            f"{understanding.urgency.level} urgency). "
            f"Lead score {reply.lead_score:.1f}, {reply.prior_exchanges} prior exchanges."
        )
        excerpt = conversation_excerpt or f"Lead: {reply.clean_body or reply.raw_body}"

        package = HandoffPackage(
            lead_id=reply.lead_id,
            reason=reason,
            urgency=understanding.urgency.level,
            summary=summary,
            suggested_response=suggested_response,
            conversation_excerpt=excerpt[:2000],
        )
        logger.info("HumanHandoff: prepared package for lead %s (%s)", reply.lead_id, reason)
        return package
