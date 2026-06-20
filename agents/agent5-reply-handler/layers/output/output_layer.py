"""
Output Layer orchestrator.

The side-effecting layer: it makes everything the action layer decided actually
happen, in order:

  1. update the conversation + record the inbound message (conversation memory),
  2. send the drafted reply to the lead (reply_sender) and log it as outbound,
  3. persist any hand-off package and notify a human (notifier),
  4. signal Agent 4 to stop the follow-up sequence when the conversation is now
     owned by Agent 5 (or a human).

Returns a small summary dict the agent rolls up into the ReplyJob.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ...config import ServiceConfig
from ...models import (
    ACTION_HUMAN_HANDOFF,
    Conversation,
    ConversationMessage,
    IncomingReply,
    ActionResult,
    Understanding,
)
from ...storage.conversation_store import ConversationStore
from .notifier import Notifier
from .reply_sender import ReplySender

logger = logging.getLogger(__name__)


class OutputLayer:
    def __init__(self, config: ServiceConfig, store: ConversationStore) -> None:
        self._config = config
        self._store = store
        self._notifier = Notifier(store)
        self._sender = ReplySender(config)
        self._signals_dir = Path(config.sequence_signals_dir)

    async def commit(
        self,
        reply: IncomingReply,
        understanding: Understanding,
        result: ActionResult,
    ) -> dict:
        summary = {"reply_sent": False, "notified": False, "escalated": False, "meeting": False}

        # 1. Conversation memory — update thread + log the inbound message.
        self._record_inbound(reply, understanding, result)

        # 2. Send the reply to the lead.
        if result.should_send_reply and result.reply_body:
            ok, message_id = await self._sender.send(
                to_email=reply.from_email,
                subject=result.reply_subject,
                body=result.reply_body,
                in_reply_to=reply.message_id,
            )
            if ok:
                summary["reply_sent"] = True
                self._record_outbound(reply, result, message_id)

        # 3. Hand-off + notifications.
        if result.handoff is not None:
            self._store.add_handoff(result.handoff)
            self._notifier.handoff(
                lead_id=reply.lead_id,
                reason=result.handoff.reason,
                urgency=result.handoff.urgency,
                summary=result.handoff.summary,
            )
            summary["notified"] = True
            summary["escalated"] = True
        elif result.should_notify_human:
            self._notifier.meeting_booked(reply.lead_id)
            summary["notified"] = True
            summary["meeting"] = True

        if result.new_lead_status == "meeting_booked":
            summary["meeting"] = True

        # 4. Tell Agent 4 to stop follow-ups now that we own the conversation.
        if result.stop_sequence:
            self._signal_stop_sequence(reply.lead_id, result.new_lead_status)

        return summary

    # ── Conversation memory ────────────────────────────────────────────────────

    def _record_inbound(
        self,
        reply: IncomingReply,
        understanding: Understanding,
        result: ActionResult,
    ) -> None:
        conv = self._store.get_conversation(reply.lead_id) or Conversation(
            id=reply.lead_id, lead_id=reply.lead_id, recipient=reply.from_email
        )
        conv.recipient = conv.recipient or reply.from_email
        conv.message_count += 1
        conv.last_intent = understanding.intent.intent
        conv.last_sentiment = understanding.sentiment.sentiment
        conv.status = result.new_lead_status
        if result.action_type == ACTION_HUMAN_HANDOFF:
            conv.escalated = True
        self._store.upsert_conversation(conv)

        self._store.add_message(
            ConversationMessage(
                id=reply.id,
                conversation_id=reply.lead_id,
                lead_id=reply.lead_id,
                direction="inbound",
                body=reply.clean_body or reply.raw_body,
                message_id=reply.message_id,
                intent=understanding.intent.intent,
                sentiment=understanding.sentiment.sentiment,
                action_taken=result.action_type,
            )
        )

    def _record_outbound(self, reply: IncomingReply, result: ActionResult, message_id: str) -> None:
        conv = self._store.get_conversation(reply.lead_id)
        if conv:
            conv.message_count += 1
            self._store.upsert_conversation(conv)
        self._store.add_message(
            ConversationMessage(
                conversation_id=reply.lead_id,
                lead_id=reply.lead_id,
                direction="outbound",
                body=result.reply_body,
                message_id=message_id,
                action_taken=result.action_type,
            )
        )

    # ── Cross-agent signal ─────────────────────────────────────────────────────

    def _signal_stop_sequence(self, lead_id: str, status: str) -> None:
        try:
            self._signals_dir.mkdir(parents=True, exist_ok=True)
            path = self._signals_dir / f"stop_{lead_id}.json"
            path.write_text(json.dumps({"lead_id": lead_id, "reason": status}, indent=2))
        except OSError as exc:
            logger.error("OutputLayer: could not write stop signal — %s", exc)

    def conversation_excerpt(self, lead_id: str, limit: int = 6) -> str:
        """Render recent messages as a transcript for a hand-off package."""
        messages = self._store.get_messages(lead_id)[-limit:]
        lines = []
        for m in messages:
            who = "Lead" if m["direction"] == "inbound" else "Us"
            lines.append(f"{who}: {m['body']}")
        return "\n".join(lines)
