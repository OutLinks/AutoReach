"""
Prompt builders for Agent 4's sequence layer.

The day-0 email is written by Agent 3. Agent 4 only generates the *follow-ups*
(day 3, day 7, day 14 breakup). Each builder returns a (system, user) tuple and
is given the original email so the follow-up stays on-thread and non-repetitive.
"""

from __future__ import annotations

from .models import STEP_DAY3, STEP_DAY7, STEP_DAY14

# Per-step guidance: the angle each follow-up should take.
_STEP_BRIEF: dict[str, str] = {
    STEP_DAY3: (
        "This is the FIRST follow-up (day 3). The lead didn't reply to the first "
        "email. Add value — share a relevant resource, stat, or quick insight. "
        "Be brief and low-pressure. Do NOT just 'bump' the thread or say 'just "
        "following up'. 40–80 words."
    ),
    STEP_DAY7: (
        "This is the SECOND follow-up (day 7). Take a DIFFERENT angle from the "
        "prior two emails — lead with social proof or a concrete result a similar "
        "company achieved. Stay short and specific. 40–80 words."
    ),
    STEP_DAY14: (
        "This is the BREAKUP email (day 14). Respectful last touch. Acknowledge "
        "they're likely busy, make it easy to say no, and leave the door open. "
        "Warm, not guilt-trippy. 30–60 words."
    ),
}


def build_followup_prompt(
    step: str,
    *,
    lead_first_name: str,
    lead_company: str,
    original_subject: str,
    original_body: str,
    sender_name: str,
    value_proposition: str = "",
    campaign_instruction: str = "",
) -> tuple[str, str]:
    """Build the (system, user) prompt for one follow-up step."""
    brief = _STEP_BRIEF.get(step, _STEP_BRIEF[STEP_DAY3])

    system = (
        "You are a cold email copywriter writing a follow-up in an existing "
        "thread. The follow-up must feel human, reference the original lightly "
        "without repeating it, and avoid spam triggers (no ALL CAPS, no "
        "exclamation marks, no 'free'/'guaranteed'/'act now').\n\n"
        f"{brief}\n\n"
        "Return ONLY a JSON object:\n"
        '{"subject": "Re: <subject>", "body": "the follow-up body with no signature"}'
    )

    user = (
        f"Lead: {lead_first_name} at {lead_company}\n"
        f"Sender: {sender_name}\n"
        f"Value proposition: {value_proposition or 'n/a'}\n\n"
        f"Campaign follow-up instructions: {campaign_instruction or 'n/a'}\n\n"
        f"ORIGINAL SUBJECT: {original_subject}\n"
        f"ORIGINAL EMAIL:\n{original_body}\n\n"
        "Write the follow-up now."
    )

    return system, user
