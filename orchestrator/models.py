"""
Data models for the Orchestrator.

The Orchestrator owns the *master record* of every lead's lifecycle — the agents
own the work, the Orchestrator owns the state. The lead lifecycle is a state
machine (see state_machine.py); these models carry the state plus everything the
six responsibilities (trigger/control/monitor/decide/optimize/report) need.

PipelineLead.id is the join key shared by all five agents (Agent 1 Lead.id flows
unchanged through Agents 2–5), so the Orchestrator can correlate artifacts across
the whole system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Lead lifecycle states ─────────────────────────────────────────────────────

NEW = "new"                     # just ingested, not yet researched
DISCOVERED = "discovered"       # Agent 1 produced it; ready for research
RESEARCHING = "researching"     # Agent 2 in progress
RESEARCHED = "researched"       # research done; ready to write
WRITING = "writing"             # Agent 3 in progress
READY = "ready"                 # email written + approved; ready to send
SENDING = "sending"             # Agent 4 in progress
SENT = "sent"                   # initial email delivered; awaiting reply
FOLLOWING_UP = "following_up"   # in the follow-up cadence
REPLIED = "replied"             # lead replied; Agent 5 owns it
HANDLING = "handling"           # Agent 5 in progress
HANDLED = "handled"             # Agent 5 resolved it
MEETING_BOOKED = "meeting_booked"  # success!
NO_REPLY = "no_reply"           # sequence exhausted, no reply
CLOSED = "closed"               # terminal: not interested / done
ERROR = "error"                 # transient failure, retry pending
DEAD = "dead"                   # permanent failure, dead-lettered for human review

# States the pipeline never moves out of automatically.
TERMINAL_STATES = {MEETING_BOOKED, CLOSED, DEAD}

# "In progress" markers an agent sets while working (used to detect timeouts).
ACTIVE_STATES = {RESEARCHING, WRITING, SENDING, HANDLING}


# ── Pipeline lead (master record) ─────────────────────────────────────────────

class PipelineLead(BaseModel):
    """One lead as the Orchestrator tracks it through the pipeline."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    state: str = NEW

    # Identity snapshot (denormalized from Agent 1 for prioritization + reporting)
    email: str = ""
    company: str = ""
    industry: str = ""

    # Prioritization inputs (0–1 each unless noted) — see control.priority_score
    quality_score: float = 0.0          # Agent 1/2 quality, normalized 0–1
    company_size_score: float = 0.0
    industry_fit_score: float = 0.0
    recency_score: float = 1.0
    manual_bump: bool = False           # human can force to the front
    priority: float = 0.0               # computed composite

    # Reliability bookkeeping
    attempts: int = 0                   # attempts at the current stage
    last_error: str = ""
    retry_after: Optional[datetime] = None

    # Lifecycle timestamps
    discovered_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    state_entered_at: datetime = Field(default_factory=datetime.utcnow)

    source_job: str = ""                # the find-job that produced this lead
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Agent run results ─────────────────────────────────────────────────────────

class StageResult(BaseModel):
    """Normalized outcome of running one stage (one agent) over a batch."""

    stage: str
    agent: str
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    # lead ids that advanced, plus any newly created lead ids (Agent 1)
    advanced_ids: list[str] = Field(default_factory=list)
    new_lead_ids: list[str] = Field(default_factory=list)
    # per-lead branch hints the decide layer consumes (lead_id → outcome tag)
    outcomes: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    ok: bool = True
    duration_seconds: float = 0.0


class RunRecord(BaseModel):
    """Persisted history of one stage invocation (for monitoring + reporting)."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    stage: str
    agent: str
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    ok: bool = True
    error: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    duration_seconds: float = 0.0


# ── Monitoring + reporting ────────────────────────────────────────────────────

class StageHealth(BaseModel):
    stage: str
    queue_depth: int = 0
    in_progress: int = 0
    error_rate: float = 0.0             # failed / processed over recent runs
    circuit: str = "closed"             # closed | open | half_open
    oldest_wait_hours: float = 0.0
    bottleneck: bool = False


class HealthSnapshot(BaseModel):
    healthy: bool = True
    stages: list[StageHealth] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    dead_letter_count: int = 0
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class DailyReport(BaseModel):
    """End-of-cycle summary the Orchestrator hands to the human."""

    funnel: dict[str, int] = Field(default_factory=dict)      # state → count
    sent_today: int = 0
    replies_today: int = 0
    meetings_booked: int = 0
    reply_rate: float = 0.0
    meeting_rate: float = 0.0
    dead_letter_count: int = 0
    alerts: list[str] = Field(default_factory=list)
    optimizations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
