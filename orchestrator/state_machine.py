"""
The lead lifecycle state machine.

Defines the legal states (in models.py), the legal transitions between them, and
the *stages* — the unit of work the Orchestrator triggers. Each stage maps an
input state to an agent and an output state:

    find     : (none)       --Agent 1-->  DISCOVERED   (creates new leads)
    research : DISCOVERED    --Agent 2-->  RESEARCHED
    write    : RESEARCHED    --Agent 3-->  READY
    send     : READY         --Agent 4-->  SENT
    followup : SENT          --Agent 4-->  FOLLOWING_UP / CLOSED
    reply    : REPLIED       --Agent 5-->  HANDLED / MEETING_BOOKED / CLOSED

The decide layer chooses the actual success state for stages with branches; this
module just declares what's legal so transitions can be validated.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import models as M


@dataclass(frozen=True)
class Stage:
    name: str
    agent: str
    from_state: str | None          # None for the find stage (creates leads)
    in_progress_state: str | None
    success_state: str


# Pipeline order — the Orchestrator runs stages in this sequence each cycle.
FIND = Stage("find", "agent1-lead-finder", None, None, M.DISCOVERED)
RESEARCH = Stage("research", "agent2-research-analyst", M.DISCOVERED, M.RESEARCHING, M.RESEARCHED)
WRITE = Stage("write", "agent3-email-writer", M.RESEARCHED, M.WRITING, M.READY)
SEND = Stage("send", "agent4-sender", M.READY, M.SENDING, M.SENT)
FOLLOWUP = Stage("followup", "agent4-sender", M.SENT, None, M.FOLLOWING_UP)
REPLY = Stage("reply", "agent5-reply-handler", M.REPLIED, M.HANDLING, M.HANDLED)

STAGES: list[Stage] = [FIND, RESEARCH, WRITE, SEND, FOLLOWUP, REPLY]
STAGE_BY_NAME: dict[str, Stage] = {s.name: s for s in STAGES}

# Stages that pull a queue of leads in a given state (everything except find).
QUEUE_STAGES: list[Stage] = [RESEARCH, WRITE, SEND, FOLLOWUP, REPLY]


# Legal transitions: from_state → set of allowed next states. Both the atomic
# forward edges (from_state → success_state, since simulated agents complete in
# one step) and the in-progress edges are allowed, plus failure (→ ERROR → DEAD)
# and the reply branch (SENT → REPLIED).
TRANSITIONS: dict[str, set[str]] = {
    M.NEW: {M.DISCOVERED, M.CLOSED},
    M.DISCOVERED: {M.RESEARCHING, M.RESEARCHED, M.CLOSED, M.ERROR},
    M.RESEARCHING: {M.RESEARCHED, M.ERROR},
    M.RESEARCHED: {M.WRITING, M.READY, M.CLOSED, M.ERROR},
    M.WRITING: {M.READY, M.CLOSED, M.ERROR},
    M.READY: {M.SENDING, M.SENT, M.CLOSED, M.ERROR},
    M.SENDING: {M.SENT, M.ERROR},
    M.SENT: {M.FOLLOWING_UP, M.REPLIED, M.NO_REPLY, M.MEETING_BOOKED, M.CLOSED, M.ERROR},
    M.FOLLOWING_UP: {M.FOLLOWING_UP, M.REPLIED, M.NO_REPLY, M.CLOSED, M.MEETING_BOOKED, M.ERROR},
    M.REPLIED: {M.HANDLING, M.HANDLED, M.MEETING_BOOKED, M.CLOSED, M.ERROR},
    M.HANDLING: {M.HANDLED, M.MEETING_BOOKED, M.CLOSED, M.ERROR},
    M.HANDLED: {M.MEETING_BOOKED, M.CLOSED},
    M.NO_REPLY: {M.CLOSED},
    # ERROR can retry back into the queue it came from, or die to the dead letter.
    M.ERROR: {
        M.DISCOVERED, M.RESEARCHED, M.READY, M.SENT, M.REPLIED,
        M.DEAD, M.CLOSED,
    },
}


def can_transition(from_state: str, to_state: str) -> bool:
    if from_state == to_state:
        return True
    return to_state in TRANSITIONS.get(from_state, set())


def available_stages(state: str, *, reply_handling_enabled: bool = True) -> list[str]:
    """Return stage actions currently legal for a lead state.

    This is a read-only view for operator tooling and the LLM orchestrator;
    actual execution still flows through ``Orchestrator.run_stage``.
    """
    names: list[str] = []
    for stage in QUEUE_STAGES:
        source_states = {stage.from_state} if stage.from_state else set()
        if stage.name == FOLLOWUP.name:
            source_states = {M.SENT, M.FOLLOWING_UP}
        if state in source_states:
            if stage.name == REPLY.name and not reply_handling_enabled:
                continue
            names.append(stage.name)
    return names


def is_terminal(state: str) -> bool:
    return state in M.TERMINAL_STATES
