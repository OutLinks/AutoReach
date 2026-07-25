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
        f"Writing tone: {voice.tone}",
        f"Sender organization: {voice.company_name}",
        f"Sender organization's value proposition: {voice.value_proposition}",
    ]
    if voice.key_messages:
        lines.append(f"Key messages: {'; '.join(voice.key_messages)}")
    if voice.forbidden_phrases:
        lines.append(f"NEVER say: {', '.join(repr(p) for p in voice.forbidden_phrases)}")
    if voice.style_rules:
        lines.append(f"Style rules: {'; '.join(voice.style_rules)}")
    sender_identity = sender.full_name
    if sender.title:
        sender_identity += f", {sender.title}"
    if sender.company:
        sender_identity += f" at {sender.company}"
    lines.append(f"Sender: {sender_identity}")
    lines.append(
        "The organization and value-proposition fields are identity/style context only. "
        "Do not turn them into a product pitch unless the campaign explicitly asks for one."
    )
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


def _campaign_system_rule(ctx: InputContext) -> str:
    instruction = ctx.campaign_instruction.strip()
    if not instruction:
        return (
            "\n\nCampaign authority: No additional campaign instruction was supplied; "
            "write truthful, relevant one-to-one outreach."
        )
    return (
        "\n\nCAMPAIGN AUTHORITY\n"
        "The campaign instruction below defines the email's purpose and overrides "
        "default sales framing, templates, value propositions, and CTA suggestions. "
        "Follow it literally. A job-interest or application email must present the "
        "sender as a candidate and must never pitch the sender's company or services. "
        "Use only supplied evidence and never invent credentials, employment history, "
        "metrics, eligibility, or personal details. Treat approved candidate proof "
        "points as a closed evidence set. A job requirement is recipient context, "
        "not evidence that the sender meets it. When the campaign authorizes only "
        "project evidence, phrase capabilities strictly as features of that project "
        "(for example, 'My AutoReach project uses FastAPI and Redis'), never as "
        "broader professional experience, expertise, successful outcomes, scale, "
        "deployment history, or domain experience.\n"
        f"{instruction}"
    )


# ── 1. Subject line ───────────────────────────────────────────────────────────

def build_subject_prompt(ctx: InputContext) -> tuple[str, str]:
    system = (
        "You write concise subject lines for one-to-one outreach. Write one subject "
        "line that accurately reflects the campaign's purpose and is relevant to the "
        "recipient.\n\n"
        "Rules:\n"
        "- Under 55 characters.\n"
        "- No clickbait, no ALL CAPS, no exclamation marks.\n"
        "- Must feel like it came from a real person, not marketing software.\n"
        "- Reference the company, role, or opportunity when useful.\n"
        "- Never imply a sales offer when the campaign is about employment.\n\n"
        "Respond with ONLY the subject line text — no quotes, no label."
    ) + _campaign_system_rule(ctx)

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
        "You write the opening line of a one-to-one outreach email.\n\n"
        "Rules:\n"
        "- One sentence only — 15 to 30 words.\n"
        "- Reference a specific, supported detail about the company, role, "
        "opportunity, product, or industry context.\n"
        "- Do not introduce yourself in the hook. That comes in the body.\n"
        "- Do not compliment generically ('I loved your website').\n"
        "- Do not start with 'I'.\n"
        "- If no named contact exists, address the hiring team or company naturally.\n\n"
        "Respond with ONLY the opening sentence."
    ) + _campaign_system_rule(ctx)

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
        "You write the body of a concise one-to-one outreach email — "
        "everything between the opening line and the CTA.\n\n"
        "Structure to follow:\n"
        "1. State who the sender is and why they are writing (1 sentence).\n"
        "2. Connect specific campaign evidence to the recipient's context (1-2 sentences).\n"
        "3. Explain the relevant fit or value truthfully (1-2 sentences).\n"
        "4. State why this recipient, role, or opportunity is relevant (1 sentence).\n\n"
        "Rules:\n"
        "- Total body: 60–130 words.\n"
        "- Write in first person as the sender.\n"
        "- Be specific — cite real details from the research.\n"
        "- Do not treat inferred research as a fact.\n"
        "- Do not repeat the opening line in the body.\n"
        "- Do not claim the recipient is struggling to hire or has a business "
        "challenge unless the source explicitly says so.\n"
        "- Keep candidate evidence separate from role requirements. Never turn a "
        "requirement into 'my experience', 'my background', or an achieved outcome.\n"
        "- If evidence is project-only, use factual wording such as 'My AutoReach "
        "project uses X'; do not say 'I successfully built', 'scalable', 'deployed', "
        "'production-ready', 'expertise', or domain-specific experience unless the "
        "approved evidence explicitly contains that claim.\n"
        "- Never sell a product or service unless the campaign explicitly requests it.\n"
        "- No bold, no bullet points, no markdown — plain prose.\n"
        "- Do not include a CTA — that will be added separately.\n\n"
        "Respond with ONLY the body text."
    ) + _campaign_system_rule(ctx)

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
        "You write the call-to-action line of a one-to-one outreach email.\n\n"
        "Rules:\n"
        "- One sentence only — low friction, specific.\n"
        "- Suggest a next step that matches the campaign's actual purpose.\n"
        "- For employment outreach, ask about the role, application process, or a "
        "brief conversation; do not ask for a sales meeting.\n"
        "- Do not use 'synergy', 'hop on a call', 'pick your brain', or 'circle back'.\n"
        "- Match the tone of the body above.\n\n"
        "Respond with ONLY the CTA sentence."
    ) + _campaign_system_rule(ctx)

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
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You review one-to-one outreach for purpose alignment and evidence-based "
        "personalization. A company or role-specific detail is sufficient when no "
        "named person was provided; do not require invented personal details.\n\n"
        "Scoring (0–10):\n"
        "  9–10: Matches campaign purpose and uses specific supported person/company/role evidence\n"
        "  7–8:  Matches campaign purpose and references specific company or role context\n"
        "  5–6:  Matches purpose but uses mostly generic context\n"
        "  3–4:  Weak purpose alignment or could be sent anywhere in the industry\n"
        "  1–2:  Wrong purpose, invented facts, or completely generic\n\n"
        "A sales pitch in an employment campaign must fail.\n\n"
        "Evidence fidelity is mandatory:\n"
        "- Treat the approved candidate proof points in the campaign purpose as a "
        "closed set.\n"
        "- Fail the email if a job requirement is restated as sender experience.\n"
        "- Fail unsupported claims of employment, expertise, success, scale, "
        "deployment, production results, metrics, domain experience, or eligibility.\n"
        "- When evidence is explicitly project-only, sender capabilities must be "
        "framed as project features, not generalized professional experience.\n"
        "- Do not suggest adding an outcome, metric, or credential absent from the "
        "approved evidence.\n\n"
        "Respond with JSON only:\n"
        '{"score": 8, "passed": true, "issues": ["list issues"], "suggestions": ["list suggestions"]}'
    )
    if campaign_instruction:
        system += (
            "\n\nCAMPAIGN PURPOSE (authoritative):\n"
            f"{campaign_instruction}"
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
        "You edit one-to-one outreach. Revise the email below to fix the listed "
        "issues while preserving its truthful campaign purpose.\n\n"
        "Return ONLY a JSON object:\n"
        '{"subject": "revised subject", "body": "full revised email body (no subject line)"}'
    ) + _campaign_system_rule(ctx)

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
