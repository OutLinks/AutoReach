"""
Data models for Agent 4: Sender + Follow-Up Manager.

Five model groups mirror the five layers:
  - ScheduledSend / SendingAccount  → Layer 1 scheduling
  - SentEmail                       → Layer 2 sending
  - TrackingEvent                   → Layer 3 tracking
  - SequenceState                   → Layer 4 sequence
  - ReputationStatus / Suppression  → Layer 5 reputation

SentEmail.email_id is the FK chain back to Agent 3 (WrittenEmail.id), which
itself links to Agent 2 (research_profile_id) and Agent 1 (lead_id) — giving a
full traceability chain: send_job → sent_email → written_email → research → lead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Sequence step taxonomy ────────────────────────────────────────────────────

# The four sequence steps and the day on which each fires after the initial send.
STEP_DAY0 = "day0_initial"
STEP_DAY3 = "day3_followup"
STEP_DAY7 = "day7_followup"
STEP_DAY14 = "day14_breakup"

SEQUENCE_STEPS = [STEP_DAY0, STEP_DAY3, STEP_DAY7, STEP_DAY14]

# Delay (in days) from the *initial* send at which each step is sent.
STEP_OFFSET_DAYS: dict[str, int] = {
    STEP_DAY0: 0,
    STEP_DAY3: 3,
    STEP_DAY7: 7,
    STEP_DAY14: 14,
}


# ── Layer 1: Scheduling ───────────────────────────────────────────────────────

class SendingAccount(BaseModel):
    """A sending identity (mailbox) used to deliver email."""

    email: str
    provider: str = "smtp"           # one of the SendingLayer registry keys
    display_name: str = ""

    # Volume governance
    daily_limit: int = 50
    hourly_limit: int = 10
    sent_today: int = 0
    sent_this_hour: int = 0

    # Health / reputation (0.0 – 1.0, higher is better)
    health_score: float = 1.0
    status: str = "active"           # "active" | "warming" | "paused"

    # Warm-up — when this mailbox first started sending. Drives the ramp.
    warmup_start_date: Optional[datetime] = None

    @property
    def is_sendable(self) -> bool:
        return self.status != "paused"


class ScheduledSend(BaseModel):
    """A single email queued for delivery at a specific local time."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    email_id: str                    # FK → Agent 3 WrittenEmail.id
    lead_id: str                     # FK → Agent 1 Lead.id
    step: str = STEP_DAY0            # one of SEQUENCE_STEPS

    recipient: str = ""
    account_email: str = ""          # which SendingAccount sends this
    timezone: str = "UTC"            # IANA tz the send time is computed in
    scheduled_at: datetime = Field(default_factory=datetime.utcnow)  # UTC instant

    status: str = "scheduled"        # "scheduled" | "sent" | "skipped" | "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Layer 2: Sending ──────────────────────────────────────────────────────────

class SendResult(BaseModel):
    """Normalized result returned by every sending provider."""

    success: bool
    provider: str
    message_id: str = ""             # provider message id (for reply threading / tracking)
    status: str = "sent"             # "sent" | "rejected" | "error"
    error: str = ""


class SentEmail(BaseModel):
    """A persisted record of one delivery attempt."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    email_id: str                    # FK → Agent 3 WrittenEmail.id
    lead_id: str                     # FK → Agent 1 Lead.id
    step: str = STEP_DAY0

    recipient: str = ""
    account_email: str = ""
    provider: str = "smtp"
    message_id: str = ""

    subject: str = ""
    body: str = ""

    # Delivery state, advanced by the tracking + reputation layers
    status: str = "queued"           # queued|sent|delivered|opened|clicked|replied
                                     # |bounced|complained|unsubscribed|failed
    opened: bool = False
    clicked: bool = False
    replied: bool = False
    bounced: bool = False

    sent_at: Optional[datetime] = None
    job_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Layer 3: Tracking ─────────────────────────────────────────────────────────

class TrackingEvent(BaseModel):
    """A single delivery/engagement event for a SentEmail."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    sent_email_id: str               # FK → SentEmail.id
    lead_id: str = ""
    event_type: str                  # "delivered"|"open"|"click"|"reply"
                                     # |"bounce"|"complaint"|"unsubscribe"
    detail: str = ""                 # e.g. clicked URL, bounce code, reply snippet
    bounce_type: str = ""            # "hard" | "soft" | "block" (bounce events only)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Layer 4: Sequence ─────────────────────────────────────────────────────────

class SequenceState(BaseModel):
    """
    Tracks where a single lead is in its 4-step follow-up sequence.

    State machine:
      active → (each step sent) → completed
      active → replied        (Agent 5 takes over; sequence paused)
      active → bounced        (hard bounce; sequence stopped)
      active → meeting_booked (stop; notify human)
      active → unsubscribed   (stop; suppress)
    """

    lead_id: str
    email_id: str                    # the day0 WrittenEmail.id this sequence started from

    current_step: str = STEP_DAY0    # the next step to send
    status: str = "active"           # active|completed|replied|bounced
                                     # |meeting_booked|unsubscribed|stopped
    steps_sent: list[str] = Field(default_factory=list)

    next_send_at: Optional[datetime] = None   # when current_step is due (UTC)
    initial_sent_at: Optional[datetime] = None

    recipient: str = ""
    account_email: str = ""
    timezone: str = "UTC"

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class FollowUpDraft(BaseModel):
    """Content generated for a single follow-up step."""

    step: str
    subject: str
    body: str
    generated_by: str = "template"   # "llm" | "template"


# ── Layer 5: Reputation ───────────────────────────────────────────────────────

class SuppressionEntry(BaseModel):
    """An address (or domain) that must never be emailed again."""

    value: str                       # email address or domain
    is_domain: bool = False
    reason: str                      # "hard_bounce"|"complaint"|"unsubscribe"|"spam_trap"
    detail: str = ""
    added_at: datetime = Field(default_factory=datetime.utcnow)


class ReputationStatus(BaseModel):
    """Aggregate sender-health snapshot for one account (or globally)."""

    account_email: str = "*"         # "*" = system-wide
    sent: int = 0
    delivered: int = 0
    bounced: int = 0
    complained: int = 0
    opened: int = 0

    bounce_rate: float = 0.0
    complaint_rate: float = 0.0
    open_rate: float = 0.0

    sender_score: float = 100.0      # 0–100 composite
    level: str = "healthy"           # "healthy" | "warning" | "critical"
    should_pause: bool = False
    reasons: list[str] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=datetime.utcnow)


# ── Job tracking ──────────────────────────────────────────────────────────────

class SendJob(BaseModel):
    """Tracks one batch send / follow-up run."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str = "initial"            # "initial" | "followups"
    status: str = "pending"          # "pending" | "in_progress" | "complete" | "paused"

    total: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    suppressed: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
