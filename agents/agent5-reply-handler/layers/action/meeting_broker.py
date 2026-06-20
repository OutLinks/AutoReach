"""
Meeting broker (Action Layer #2).

Handles the happy path. For an INTERESTED lead it drafts a reply that includes
the booking link. For a lead who says a meeting is already booked, it drafts a
confirmation and flags the conversation so a human is notified and the follow-up
sequence stops.
"""

from __future__ import annotations

import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import INTENT_MEETING_BOOKED, IncomingReply
from ...prompt import build_reply_prompt

logger = logging.getLogger(__name__)


class MeetingBroker:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def broker(self, reply: IncomingReply, intent: str) -> tuple[str, bool]:
        """
        Returns (reply_body, notify_human).

        notify_human is True for already-booked meetings (the doc wants a human
        confirmation + sequence stop).
        """
        if intent == INTENT_MEETING_BOOKED:
            return self._confirmation(reply), True
        return await self._invite(reply), False

    async def _invite(self, reply: IncomingReply) -> str:
        link = self._config.calendly_link
        if self._config.use_llm_replies:
            system, user = build_reply_prompt(
                reply,
                "INTERESTED",
                sender_name=self._config.sender_name,
                calendly_link=link,
                guidance="They're interested. Be warm and brief, and include the booking link.",
            )
            try:
                adapter = get_model(self._config.model)
                response = await adapter.complete(
                    self._config.model,
                    [Message(role="system", content=system), Message(role="user", content=user)],
                )
                body = (response.content or "").strip()
                if body:
                    return body
            except Exception as exc:
                logger.warning("MeetingBroker: LLM invite failed — %s", exc)

        name = reply.lead_first_name or "there"
        return (
            f"Hi {name},\n\nThanks for getting back — I'd love to show you how this works. "
            f"Here's my calendar, grab whatever time suits you:\n{link}\n\n"
            f"Looking forward to it,\n{self._config.sender_name}"
        )

    def _confirmation(self, reply: IncomingReply) -> str:
        name = reply.lead_first_name or "there"
        return (
            f"Hi {name},\n\nPerfect — got it on the calendar. I'll send a quick agenda "
            f"beforehand so we make the most of the time. Talk soon!\n\n{self._config.sender_name}"
        )
