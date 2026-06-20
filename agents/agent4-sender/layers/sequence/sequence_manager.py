"""
Sequence Layer orchestrator — the follow-up state machine.

Tracks each lead through the 4-touch cadence and decides what is due:

  active → (day0 sent) → day3 → day7 → day14 → completed

Terminal transitions can fire at any point:
  replied → Agent 5 takes over (pause)
  bounced → hard bounce, stop
  meeting_booked → stop + notify human
  unsubscribed → stop + suppress

Persistence lives in SendStore; this class owns the transition logic only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...config import ServiceConfig
from ...models import STEP_DAY0, SequenceState
from ...storage.send_store import SendStore
from . import steps
from .followup_writer import FollowUpWriter

logger = logging.getLogger(__name__)


class SequenceLayer:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store
        self.writer = FollowUpWriter(config)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(
        self,
        *,
        lead_id: str,
        email_id: str,
        sent_at: datetime,
        recipient: str,
        account_email: str,
        timezone_name: str = "UTC",
    ) -> SequenceState:
        """Begin a sequence right after the day-0 email is sent."""
        first_followup = steps.next_step(STEP_DAY0) or STEP_DAY0
        state = SequenceState(
            lead_id=lead_id,
            email_id=email_id,
            current_step=first_followup,
            status="active",
            steps_sent=[STEP_DAY0],
            initial_sent_at=sent_at,
            next_send_at=self._due_at(sent_at, first_followup),
            recipient=recipient,
            account_email=account_email,
            timezone=timezone_name,
        )
        self._store.upsert_sequence(state)
        logger.info(
            "SequenceLayer: started sequence for lead %s (next=%s @ %s)",
            lead_id, first_followup, state.next_send_at,
        )
        return state

    def due(self, now: datetime | None = None) -> list[SequenceState]:
        """Active sequences whose current step is due to send."""
        now = now or datetime.now(timezone.utc)
        out = []
        for state in self._store.list_active_sequences():
            nxt = state.next_send_at
            if nxt is None:
                continue
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=timezone.utc)
            if nxt <= now:
                out.append(state)
        return out

    def advance(self, state: SequenceState, sent_at: datetime) -> SequenceState:
        """Record that the current step was sent and queue the next one."""
        state.steps_sent.append(state.current_step)
        following = steps.next_step(state.current_step)
        if following is None:
            state.status = "completed"
            state.next_send_at = None
            logger.info("SequenceLayer: lead %s sequence completed", state.lead_id)
        else:
            state.current_step = following
            base = state.initial_sent_at or sent_at
            state.next_send_at = self._due_at(base, following)
        self._store.upsert_sequence(state)
        return state

    # ── Terminal transitions ───────────────────────────────────────────────────

    def pause_on_reply(self, lead_id: str) -> None:
        self._transition(lead_id, "replied")

    def mark_bounced(self, lead_id: str) -> None:
        self._transition(lead_id, "bounced")

    def mark_unsubscribed(self, lead_id: str) -> None:
        self._transition(lead_id, "unsubscribed")

    def mark_meeting_booked(self, lead_id: str) -> None:
        self._transition(lead_id, "meeting_booked")

    def stop(self, lead_id: str) -> None:
        self._transition(lead_id, "stopped")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _transition(self, lead_id: str, status: str) -> None:
        state = self._store.get_sequence(lead_id)
        if not state or not state.is_active:
            return
        state.status = status
        state.next_send_at = None
        self._store.upsert_sequence(state)
        logger.info("SequenceLayer: lead %s → %s", lead_id, status)

    def _due_at(self, base: datetime, step: str) -> datetime:
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        return base + timedelta(days=steps.offset_days(step))
