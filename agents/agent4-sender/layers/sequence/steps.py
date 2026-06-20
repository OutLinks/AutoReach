"""
Sequence step definitions (Sequence Layer #1–4).

The four touches and their cadence, plus a deterministic template fallback for
each follow-up used when the LLM writer is disabled or fails. Day 0 is not
templated here — that body comes from Agent 3.
"""

from __future__ import annotations

from ...models import (
    STEP_DAY0,
    STEP_DAY3,
    STEP_DAY7,
    STEP_DAY14,
    STEP_OFFSET_DAYS,
)


def offset_days(step: str) -> int:
    """Days after the initial send at which this step fires."""
    return STEP_OFFSET_DAYS.get(step, 0)


def next_step(step: str) -> str | None:
    """The step that follows `step`, or None if `step` is the last one."""
    order = [STEP_DAY0, STEP_DAY3, STEP_DAY7, STEP_DAY14]
    try:
        idx = order.index(step)
    except ValueError:
        return None
    return order[idx + 1] if idx + 1 < len(order) else None


def template_followup(
    step: str,
    *,
    first_name: str,
    company: str,
    original_subject: str,
    sender_name: str,
    value_proposition: str = "",
) -> tuple[str, str]:
    """Return (subject, body) for a follow-up without calling the LLM."""
    subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
    vp = value_proposition or "what we do"

    if step == STEP_DAY3:
        body = (
            f"Hi {first_name},\n\n"
            f"Wanted to bubble this back up in case it got buried. I put together a "
            f"short rundown of how teams like {company} have approached this — happy "
            f"to send it over if useful.\n\n"
            f"Worth a quick look?\n\n{sender_name}"
        )
    elif step == STEP_DAY7:
        body = (
            f"Hi {first_name},\n\n"
            f"One more angle: a company similar to {company} used {vp} to get a "
            f"measurable lift in a few weeks. Different situations, but the pattern "
            f"tends to hold.\n\n"
            f"Open to a 15-minute walkthrough?\n\n{sender_name}"
        )
    else:  # STEP_DAY14 breakup
        body = (
            f"Hi {first_name},\n\n"
            f"I don't want to keep cluttering your inbox, so this is my last note. "
            f"If the timing isn't right, no worries at all — feel free to reach out "
            f"whenever it makes sense.\n\n"
            f"Wishing {company} the best,\n{sender_name}"
        )

    return subject, body
