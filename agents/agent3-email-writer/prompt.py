"""
Prompt builders for Agent 3: Email Writer.

One builder per writing component (subject, hook, body, CTA) plus one
for the personalization quality check. Each returns a (system, user) tuple.
"""

from __future__ import annotations

from .models import BrandVoice, InputContext, SenderProfile


# ── Shared context block ──────────────────────────────────────────────────────

def _voice_block(voice: BrandVoice, sender: SenderProfile) -> str:
    lines = [
        f"Brand voice: {voice.tone}",
        f"Company: {voice.company_name}",
        f"Value proposition: {voice.value_proposition}",
    ]
    if voice.key_messages:
        lines.append(f"Key messages: {'; '.join(voice.key_messages)}")
    if voice.forbidden_phrases:
        lines.append(f"NEVER say: {', '.join(repr(p) for p in voice.forbidden_phrases)}")
    if voice.style_rules:
        lines.append(f"Style rules: {'; '.join(voice.style_rules)}")
    lines.append(f"Sender: {sender.full_name}, {sender.title} at {sender.company}")
    return "\n".join(lines)


def _lead_block(ctx: InputContext) -> str:
    return (
        f"Lead: {ctx.lead_first_name} {ctx.lead_last_name}, "
        f"{ctx.lead_title} at {ctx.lead_company}"
    )


def _campaign_block(ctx: InputContext) -> str:
    return (
        f"\nCampaign instructions: {ctx.campaign_instruction}"
        if ctx.campaign_instruction
        else ""
    )


# ── 1. Subject line ───────────────────────────────────────────────────────────

def build_subject_prompt(ctx: InputContext) -> tuple[str, str]:
    system = (
        "You are a cold email subject line specialist. Write one subject line "
        "that is specific to this person, creates curiosity or relevance, and "
        "avoids spam triggers.\n\n"
        "Rules:\n"
        "- Under 55 characters.\n"
        "- No clickbait, no ALL CAPS, no exclamation marks.\n"
        "- Must feel like it came from a real person, not marketing software.\n"
        "- Reference something specific to their company or role when possible.\n\n"
        "Respond with ONLY the subject line text — no quotes, no label."
    )

    ideas = ""
    if ctx.subject_line_ideas:
        ideas = "\n\nInspiration (improve on one of these, don't copy directly):\n" + \
                "\n".join(f"- {s}" for s in ctx.subject_line_ideas[:3])

    user = (
        f"{_lead_block(ctx)}\n"
        f"Their company: {ctx.lead_company}\n"
        f"Company summary: {ctx.company_summary[:500]}\n"
        f"Pain point angle: {ctx.pain_points_summary[:300]}\n"
        f"{_voice_block(ctx.brand_voice, ctx.sender)}"
        f"{_campaign_block(ctx)}"
        f"{ideas}"
    )

    return system, user


# ── 2. Hook (opening line) ────────────────────────────────────────────────────

def build_hook_prompt(ctx: InputContext, subject: str) -> tuple[str, str]:
    system = (
        "You are a cold email copywriter. Write the opening line of a cold email.\n\n"
        "Rules:\n"
        "- One sentence only — 15 to 30 words.\n"
        "- Must reference something SPECIFIC to this person or company "
        "(a recent event, their role, their product, their industry context).\n"
        "- Do not introduce yourself in the hook. That comes in the body.\n"
        "- Do not compliment generically ('I loved your website').\n"
        "- Do not start with 'I'.\n\n"
        "Respond with ONLY the opening sentence."
    )

    hook_ref = f"\nResearch-backed hook idea: {ctx.recommended_hook}" if ctx.recommended_hook else ""
    hooks = "\n".join(f"- {h}" for h in ctx.personal_hooks[:3]) if ctx.personal_hooks else ""

    user = (
        f"Subject used: {subject}\n"
        f"{_lead_block(ctx)}\n"
        f"Company: {ctx.company_summary[:400]}\n"
        f"Personal details to reference: {hooks}\n"
        f"{hook_ref}\n"
        f"{_voice_block(ctx.brand_voice, ctx.sender)}"
        f"{_campaign_block(ctx)}"
    )

    return system, user


# ── 3. Body ───────────────────────────────────────────────────────────────────

def build_body_prompt(ctx: InputContext, subject: str, hook: str) -> tuple[str, str]:
    system = (
        "You are a cold email copywriter. Write the body of a cold email — "
        "everything between the opening line and the CTA.\n\n"
        "Structure to follow:\n"
        "1. Brief intro (name, company, what you do — 1 sentence).\n"
        "2. Bridge: connect their specific situation to the pain point (1-2 sentences).\n"
        "3. Pain acknowledgment: reference the specific pain point with evidence (1-2 sentences).\n"
        "4. Value: explain how you address this specifically — no generic claims (1-2 sentences).\n\n"
        "Rules:\n"
        "- Total body: 60–130 words.\n"
        "- Write in first person as the sender.\n"
        "- Be specific — cite real details from the research.\n"
        "- No bold, no bullet points, no markdown — plain prose.\n"
        "- Do not include a CTA — that will be added separately.\n\n"
        "Respond with ONLY the body text."
    )

    user = (
        f"Subject: {subject}\n"
        f"Opening line already written: {hook}\n\n"
        f"{_lead_block(ctx)}\n"
        f"Company context: {ctx.company_summary[:600]}\n"
        f"Pain point to address: {ctx.pain_points_summary[:500]}\n"
        f"Template structure: {ctx.template.structure_guide}\n\n"
        f"{_voice_block(ctx.brand_voice, ctx.sender)}"
        f"{_campaign_block(ctx)}"
    )

    return system, user


# ── 4. CTA ────────────────────────────────────────────────────────────────────

def build_cta_prompt(ctx: InputContext, body: str) -> tuple[str, str]:
    system = (
        "You are a cold email copywriter. Write the call-to-action line of a cold email.\n\n"
        "Rules:\n"
        "- One sentence only — low friction, specific.\n"
        "- Suggest a concrete next step (15-min call, quick question, reply with yes/no).\n"
        "- Do not use 'synergy', 'hop on a call', 'pick your brain', or 'circle back'.\n"
        "- Match the tone of the body above.\n\n"
        "Respond with ONLY the CTA sentence."
    )

    cta_ref = (
        f"\nPreferred CTA type: {ctx.recommended_cta}" if ctx.recommended_cta else ""
    )

    user = (
        f"Email body written so far:\n{body}\n\n"
        f"{_lead_block(ctx)}\n"
        f"{cta_ref}\n"
        f"{_voice_block(ctx.brand_voice, ctx.sender)}"
        f"{_campaign_block(ctx)}"
    )

    return system, user


# ── 5. Personalization check ──────────────────────────────────────────────────

def build_personalization_check_prompt(
    full_email: str,
    lead_name: str,
    company_name: str,
    pain_points_summary: str,
    personal_hooks: list[str],
) -> tuple[str, str]:
    system = (
        "You are a cold email quality reviewer. Evaluate how personalized this email is "
        "— specifically whether it references real details about this person and company.\n\n"
        "Scoring (0–10):\n"
        "  9–10: Specific reference to the person's background/activity AND company-specific pain\n"
        "  7–8:  References company context and mentions a specific pain point\n"
        "  5–6:  Mentions company name but pain point is generic\n"
        "  3–4:  Could be sent to anyone in the same industry\n"
        "  1–2:  Completely generic\n\n"
        "Respond with JSON only:\n"
        '{"score": 8, "passed": true, "issues": ["list issues"], "suggestions": ["list suggestions"]}'
    )

    hooks = "\n".join(f"- {h}" for h in personal_hooks[:3]) if personal_hooks else "none"

    user = (
        f"Lead: {lead_name} at {company_name}\n"
        f"Research hooks available: {hooks}\n"
        f"Pain point: {pain_points_summary[:300]}\n\n"
        f"EMAIL:\n{full_email}"
    )

    return system, user


# ── 6. Revision prompt ────────────────────────────────────────────────────────

def build_revision_prompt(
    full_email: str,
    subject: str,
    issues: list[str],
    suggestions: list[str],
    ctx: InputContext,
) -> tuple[str, str]:
    system = (
        "You are a cold email editor. Revise the email below to fix the listed issues.\n\n"
        "Return ONLY a JSON object:\n"
        '{"subject": "revised subject", "body": "full revised email body (no subject line)"}'
    )

    issue_list = "\n".join(f"- {i}" for i in issues)
    suggestion_list = "\n".join(f"- {s}" for s in suggestions)

    user = (
        f"SUBJECT: {subject}\n\n"
        f"BODY:\n{full_email}\n\n"
        f"ISSUES TO FIX:\n{issue_list}\n\n"
        f"SUGGESTIONS:\n{suggestion_list}\n\n"
        f"{_lead_block(ctx)}\n"
        f"{_voice_block(ctx.brand_voice, ctx.sender)}"
        f"{_campaign_block(ctx)}"
    )

    return system, user
