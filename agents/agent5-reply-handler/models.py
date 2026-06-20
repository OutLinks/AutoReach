"""
Data models for Agent 5: Reply Handler.

Four model groups mirror the four layers:
  - IncomingReply      → Layer 1 input (a parsed reply + conversation context)
  - Understanding       → Layer 2 understanding (intent + sentiment + urgency)
  - ActionResult        → Layer 3 action (what to do + drafted response)
  - Conversation/Message/Handoff/Notification → Layer 4 output (persisted state)

IncomingReply.lead_id is the FK chain back to Agents 1–4: the reply links to a
SentEmail (Agent 4) → WrittenEmail (Agent 3) → ResearchProfile (Agent 2) → Lead
(Agent 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Intent taxonomy (architecture doc) ────────────────────────────────────────

INTENT_INTERESTED = "INTERESTED"
INTENT_NOT_INTERESTED = "NOT_INTERESTED"
INTENT_QUESTION = "QUESTION"
INTENT_OBJECTION = "OBJECTION"
INTENT_WRONG_PERSON = "WRONG_PERSON"
INTENT_OUT_OF_OFFICE = "OUT_OF_OFFICE"
INTENT_ALREADY_CUSTOMER = "ALREADY_CUSTOMER"
INTENT_NEEDS_TIME = "NEEDS_TIME"
INTENT_CONFUSED = "CONFUSED"
INTENT_ANGRY = "ANGRY"
INTENT_MEETING_BOOKED = "MEETING_BOOKED"
INTENT_UNCLEAR = "UNCLEAR"

ALL_INTENTS = [
    INTENT_INTERESTED, INTENT_NOT_INTERESTED, INTENT_QUESTION, INTENT_OBJECTION,
    INTENT_WRONG_PERSON, INTENT_OUT_OF_OFFICE, INTENT_ALREADY_CUSTOMER,
    INTENT_NEEDS_TIME, INTENT_CONFUSED, INTENT_ANGRY, INTENT_MEETING_BOOKED,
    INTENT_UNCLEAR,
]

# ── Action taxonomy ───────────────────────────────────────────────────────────

ACTION_AUTO_REPLY = "auto_reply"
ACTION_BOOK_MEETING = "book_meeting"
ACTION_HANDLE_OBJECTION = "handle_objection"
ACTION_HUMAN_HANDOFF = "human_handoff"
ACTION_STOP = "stop"            # polite goodbye / unsubscribe, no further action
ACTION_NONE = "none"           # e.g. out-of-office: just pause and wait


# ── Layer 1: Input ────────────────────────────────────────────────────────────

class IncomingReply(BaseModel):
    """A single reply to handle, with the context needed to understand it."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    lead_id: str
    conversation_id: str = ""           # defaults to lead_id when absent

    # Source linkage (from Agent 4's reply hand-off)
    sent_email_id: str = ""             # FK → Agent 4 SentEmail.id
    email_id: str = ""                  # FK → Agent 3 WrittenEmail.id
    message_id: str = ""                # original thread Message-ID (for threading)

    from_email: str = ""
    raw_body: str = ""                  # the reply as received
    clean_body: str = ""                # quoted text + signature stripped

    # Context assembled by the input layer
    lead_first_name: str = ""
    lead_company: str = ""
    lead_score: float = 0.0             # Agent 1 lead score (0–10) for escalation
    original_subject: str = ""
    original_body: str = ""             # the email they're replying to
    prior_exchanges: int = 0            # how many back-and-forths so far

    received_at: datetime = Field(default_factory=datetime.utcnow)


# ── Layer 2: Understanding ────────────────────────────────────────────────────

class IntentResult(BaseModel):
    intent: str = INTENT_UNCLEAR
    confidence: float = 0.0             # 0.0 – 1.0
    reasoning: str = ""


class SentimentResult(BaseModel):
    sentiment: str = "neutral"          # "positive" | "neutral" | "negative"
    score: float = 0.0                  # -1.0 (very negative) … +1.0 (very positive)


class UrgencyResult(BaseModel):
    level: str = "low"                  # "low" | "medium" | "high"
    reasons: list[str] = Field(default_factory=list)


class Understanding(BaseModel):
    """Bundled output of the understanding layer for one reply."""

    intent: IntentResult = Field(default_factory=IntentResult)
    sentiment: SentimentResult = Field(default_factory=SentimentResult)
    urgency: UrgencyResult = Field(default_factory=UrgencyResult)


class ActionDecision(BaseModel):
    """The decision-maker's call: what to do and whether AI may do it alone."""

    action_type: str = ACTION_HUMAN_HANDOFF
    auto_handle: bool = False           # False → route to a human
    confidence: float = 0.0
    reason: str = ""
    escalation_reasons: list[str] = Field(default_factory=list)


# ── Layer 3: Action ───────────────────────────────────────────────────────────

class HandoffPackage(BaseModel):
    """Everything a human needs to take over the conversation."""

    lead_id: str
    reason: str
    urgency: str = "medium"
    summary: str = ""
    suggested_response: str = ""
    conversation_excerpt: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ActionResult(BaseModel):
    """Outcome of the action layer — a drafted response and/or a handoff."""

    action_type: str
    reply_subject: str = ""
    reply_body: str = ""
    calendly_link: str = ""
    handoff: Optional[HandoffPackage] = None

    should_send_reply: bool = False     # send reply_body to the lead?
    should_notify_human: bool = False
    stop_sequence: bool = True          # tell Agent 4 to stop follow-ups for this lead
    new_lead_status: str = "handled"    # status to write back for the lead


# ── Layer 4: Output (persisted) ───────────────────────────────────────────────

class ConversationMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    lead_id: str
    direction: str                      # "inbound" | "outbound"
    body: str
    message_id: str = ""
    intent: str = ""                    # set on inbound messages
    sentiment: str = ""
    action_taken: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(BaseModel):
    id: str                             # = lead_id
    lead_id: str
    recipient: str = ""
    status: str = "active"              # active|handled|meeting_booked|closed|escalated
    message_count: int = 0
    last_intent: str = ""
    last_sentiment: str = ""
    escalated: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str                           # "handoff" | "meeting_booked" | "alert"
    lead_id: str = ""
    title: str = ""
    message: str = ""
    urgency: str = "medium"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReplyJob(BaseModel):
    """Tracks one batch reply-handling run."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    status: str = "pending"             # pending|in_progress|complete
    total: int = 0
    handled: int = 0                    # auto-handled by AI
    escalated: int = 0                  # routed to a human
    replies_sent: int = 0
    meetings_booked: int = 0
    skipped: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
