"""
Responsibility 4 — DECIDE: branching logic and edge cases.

Translates a stage's per-lead outcomes into the next lifecycle state, applying
the quality gates from config:

  research : ok            → RESEARCHED          incomplete → CLOSED
  write    : email_score≥t → READY               below t / low_quality → CLOSED
  send     : sent          → SENT                 bounced → CLOSED (invalid)
  followup : continued     → FOLLOWING_UP         exhausted → NO_REPLY
  reply    : meeting_booked → MEETING_BOOKED      handled/escalated → HANDLED
             closed        → CLOSED

Returns a mapping of lead_id → target_state the engine then transitions (with
validation against the state machine).
"""

from __future__ import annotations

import logging

from ..config import OrchestratorConfig
from ..models import (
    CLOSED, FOLLOWING_UP, HANDLED, MEETING_BOOKED, NO_REPLY, READY,
    RESEARCHED, SENT,
)
from ..state_machine import (
    FOLLOWUP, REPLY, RESEARCH, SEND, WRITE, Stage,
)

logger = logging.getLogger(__name__)


class Decide:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._config = config

    def resolve(self, stage: Stage, outcomes: dict[str, str]) -> dict[str, str]:
        """lead_id → target_state for every lead with an outcome."""
        fn = {
            RESEARCH.name: self._research,
            WRITE.name: self._write,
            SEND.name: self._send,
            FOLLOWUP.name: self._followup,
            REPLY.name: self._reply,
        }.get(stage.name)
        if fn is None:
            return {}
        return {lid: fn(outcome) for lid, outcome in outcomes.items()}

    # ── Per-stage rules ─────────────────────────────────────────────────────────

    def _research(self, outcome: str) -> str:
        return RESEARCHED if outcome == "ok" else CLOSED

    def _write(self, outcome: str) -> str:
        if outcome.startswith("email_score="):
            try:
                score = float(outcome.split("=", 1)[1])
            except ValueError:
                score = 0.0
            return READY if score >= self._config.quality.min_email_score else CLOSED
        return CLOSED  # "low_quality" or anything unexpected

    def _send(self, outcome: str) -> str:
        if outcome == "sent":
            return SENT
        if outcome == "not_sent":
            return READY  # temporary capacity/provider skip; retry later
        return CLOSED  # bounce or invalid recipient

    def _followup(self, outcome: str) -> str:
        return NO_REPLY if outcome == "exhausted" else FOLLOWING_UP

    def _reply(self, outcome: str) -> str:
        if outcome == "meeting_booked":
            return MEETING_BOOKED
        if outcome == "closed":
            return CLOSED
        return HANDLED  # handled or escalated (human now owns it)
