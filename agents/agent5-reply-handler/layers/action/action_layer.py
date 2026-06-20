"""
Action Layer orchestrator.

Turns an ActionDecision into a concrete ActionResult — a drafted reply and/or a
human hand-off package — by dispatching to the right component:

  book_meeting     → meeting_broker
  handle_objection → objection_handler
  auto_reply       → auto_reply_generator
  stop             → auto_reply_generator (polite goodbye)
  none             → no reply (e.g. out-of-office: just pause)
  human_handoff    → human_handoff (no message to the lead)

It owns the side-effect flags (send reply? notify human? stop the sequence? what
status to write back) but performs no I/O — the output layer does that.
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
    INTENT_MEETING_BOOKED,
    INTENT_NEEDS_TIME,
    INTENT_QUESTION,
    INTENT_WRONG_PERSON,
    ActionDecision,
    ActionResult,
    IncomingReply,
    Understanding,
)
from .auto_reply import AutoReplyGenerator
from .meeting_broker import MeetingBroker
from .objection_handler import ObjectionHandler
from .human_handoff import HumanHandoff

logger = logging.getLogger(__name__)


class ActionLayer:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._auto_reply = AutoReplyGenerator(config)
        self._meeting = MeetingBroker(config)
        self._objection = ObjectionHandler(config)
        self._handoff = HumanHandoff()

    async def act(
        self,
        reply: IncomingReply,
        understanding: Understanding,
        decision: ActionDecision,
        conversation_excerpt: str = "",
    ) -> ActionResult:
        action = decision.action_type
        intent = understanding.intent.intent
        subject = self._reply_subject(reply)

        if action == ACTION_BOOK_MEETING:
            body, notify = await self._meeting.broker(reply, intent)
            booked = intent == INTENT_MEETING_BOOKED
            return ActionResult(
                action_type=action,
                reply_subject=subject,
                reply_body=body,
                calendly_link=self._config.calendly_link,
                should_send_reply=True,
                should_notify_human=notify,
                stop_sequence=True,
                new_lead_status="meeting_booked" if booked else "interested",
            )

        if action == ACTION_HANDLE_OBJECTION:
            body = await self._objection.handle(reply)
            return ActionResult(
                action_type=action,
                reply_subject=subject,
                reply_body=body,
                calendly_link=self._config.calendly_link,
                should_send_reply=True,
                stop_sequence=True,
                new_lead_status="active",
            )

        if action in (ACTION_AUTO_REPLY, ACTION_STOP):
            include_link = intent in (INTENT_QUESTION,)
            body = await self._auto_reply.generate(reply, intent, include_link)
            return ActionResult(
                action_type=action,
                reply_subject=subject,
                reply_body=body,
                should_send_reply=True,
                stop_sequence=True,
                new_lead_status="closed" if action == ACTION_STOP else (
                    "nurture" if intent == INTENT_NEEDS_TIME else "active"
                ),
            )

        if action == ACTION_NONE:
            # Out-of-office and similar: don't reply, don't escalate, let Agent 4
            # resume the sequence later.
            return ActionResult(
                action_type=action,
                should_send_reply=False,
                stop_sequence=False,
                new_lead_status="active",
            )

        # Default: human handoff.
        package = self._handoff.build(reply, understanding, decision, conversation_excerpt)
        return ActionResult(
            action_type=ACTION_HUMAN_HANDOFF,
            handoff=package,
            should_send_reply=False,
            should_notify_human=True,
            stop_sequence=True,
            new_lead_status="escalated",
        )

    @staticmethod
    def _reply_subject(reply: IncomingReply) -> str:
        subj = reply.original_subject or "your email"
        return subj if subj.lower().startswith("re:") else f"Re: {subj}"
