"""
Prompt builders for Agent 5: Reply Handler.

Two families:
  - classification (intent) — returns strict JSON the understanding layer parses,
  - generation (auto-reply, objection) — returns the reply body text.

Each builder returns a (system, user) tuple.
"""

from __future__ import annotations

from .models import ALL_INTENTS, IncomingReply


def _context_block(reply: IncomingReply) -> str:
    return (
        f"Lead: {reply.lead_first_name or 'the lead'} at "
        f"{reply.lead_company or 'their company'}\n"
        f"Original email subject: {reply.original_subject}\n"
        f"Prior exchanges in this thread: {reply.prior_exchanges}\n"
        f"Their reply:\n\"\"\"\n{reply.clean_body or reply.raw_body}\n\"\"\""
    )


# ── Intent classification ─────────────────────────────────────────────────────

def build_intent_prompt(
    reply: IncomingReply, campaign_instruction: str = ""
) -> tuple[str, str]:
    intents = ", ".join(ALL_INTENTS)
    system = (
        "You are a sales-reply classifier. Read the lead's reply and classify the "
        "intent into exactly one category.\n\n"
        f"Categories: {intents}\n\n"
        "Guidance:\n"
        "- INTERESTED: wants to learn more, asks to meet, says yes.\n"
        "- NOT_INTERESTED: declines, not a fit, 'no thanks'.\n"
        "- QUESTION: asks a specific question before committing.\n"
        "- OBJECTION: raises a concern (price, timing, already have a solution).\n"
        "- WRONG_PERSON: not the right contact; points elsewhere.\n"
        "- OUT_OF_OFFICE: automated away/vacation reply.\n"
        "- ALREADY_CUSTOMER: already uses your product/service.\n"
        "- NEEDS_TIME: interested but not now; 'circle back later'.\n"
        "- CONFUSED: doesn't understand who you are or what you offer.\n"
        "- ANGRY: upset/hostile about being emailed.\n"
        "- MEETING_BOOKED: confirms a meeting is already booked.\n"
        "- UNCLEAR: cannot determine intent.\n\n"
        "Respond with JSON only:\n"
        '{"intent": "QUESTION", "confidence": 0.0-1.0, "reasoning": "one sentence"}'
    )
    return system, f"{_context_block(reply)}\n\nCampaign instructions: {campaign_instruction or 'n/a'}"


# ── Auto-reply generation ─────────────────────────────────────────────────────

def build_reply_prompt(
    reply: IncomingReply,
    intent: str,
    *,
    sender_name: str,
    calendly_link: str = "",
    guidance: str = "",
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You are writing a short, warm, human reply to a sales lead's response. "
        "Match their tone, be concise (2–5 sentences), never pushy, no spam "
        "phrasing, no exclamation overload.\n\n"
        f"The lead's intent is: {intent}.\n"
        f"{guidance}\n\n"
        "If a meeting link is provided and the intent warrants it, include the link "
        "naturally. Sign off as the sender. Respond with ONLY the reply body."
    )

    link = f"\nMeeting link to include if relevant: {calendly_link}" if calendly_link else ""
    user = (
        f"{_context_block(reply)}\n"
        f"Sender name (sign as this): {sender_name}{link}\n"
        f"Campaign reply instructions: {campaign_instruction or 'n/a'}\n\n"
        "Write the reply now."
    )
    return system, user


# ── Objection handling ────────────────────────────────────────────────────────

def build_objection_prompt(
    reply: IncomingReply,
    objection_type: str,
    strategy: str,
    *,
    sender_name: str,
    calendly_link: str = "",
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You are a thoughtful sales rep replying to an objection. Acknowledge the "
        "concern genuinely, then reframe with a brief, concrete point. Do not "
        "badmouth competitors. Keep it under 6 sentences and low-pressure.\n\n"
        f"Objection type: {objection_type}\n"
        f"Recommended strategy: {strategy}\n\n"
        "End with a soft, optional next step (offer the meeting link if provided). "
        "Respond with ONLY the reply body."
    )
    link = f"\nMeeting link: {calendly_link}" if calendly_link else ""
    user = (
        f"{_context_block(reply)}\n"
        f"Sender name (sign as this): {sender_name}{link}\n"
        f"Campaign reply instructions: {campaign_instruction or 'n/a'}\n\n"
        "Write the objection-handling reply now."
    )
    return system, user
