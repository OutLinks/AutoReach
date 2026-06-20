"""
Prompt builders for Agent 2's five AI analysis tasks.

Each builder takes the raw research data (and lead context) and returns
a fully formed (system, user) tuple ready for the model selection layer.
The system prompt adapts to which data sources actually returned results.
"""

from __future__ import annotations

from .config import ServiceConfig
from .models import RawResearchData


# ── Shared data-context builder ───────────────────────────────────────────────

def _build_data_context(data: RawResearchData) -> str:
    """Assembles all available raw data into a single labelled context block."""
    sections: list[str] = []

    if data.website_content:
        sections.append(f"## WEBSITE CONTENT\n{data.website_content[:40_000]}")

    if data.linkedin_person:
        sections.append(f"## LINKEDIN — PERSON\n{data.linkedin_person[:10_000]}")

    if data.linkedin_company:
        sections.append(f"## LINKEDIN — COMPANY\n{data.linkedin_company[:10_000]}")

    if data.news_snippets:
        snippets = "\n".join(f"- {s}" for s in data.news_snippets[:20])
        sections.append(f"## NEWS ARTICLES\n{snippets}")

    if data.social_posts:
        posts = "\n".join(f"- {p}" for p in data.social_posts[:10])
        sections.append(f"## RECENT POSTS\n{posts}")

    if not sections:
        return "## DATA\n(No data was collected for this lead.)"

    return "\n\n".join(sections)


# ── 1. Company Profile ────────────────────────────────────────────────────────

def build_company_profile_prompt(
    lead_name: str,
    company_name: str,
    data: RawResearchData,
) -> tuple[str, str]:
    system = (
        "You are a B2B market intelligence analyst. Your job is to produce a "
        "tight, accurate company profile from scraped data.\n\n"
        "Rules:\n"
        "- Be specific. Avoid generic statements like 'they focus on customer success'.\n"
        "- Cite only what you found in the data. Do not invent facts.\n"
        "- growth_stage must be one of: startup, scaling, established.\n\n"
        "Respond ONLY with a JSON object matching this schema:\n"
        "{\n"
        '  "summary": "2–3 sentence company description",\n'
        '  "target_customer": "who they sell to",\n'
        '  "business_model": "how they make money",\n'
        '  "growth_stage": "startup|scaling|established",\n'
        '  "competitive_advantage": "what sets them apart",\n'
        '  "recent_events": ["funding round", "expansion", "product launch"],\n'
        '  "market_position": "how they sit vs competitors"\n'
        "}"
    )

    user = (
        f"Analyze this company: **{company_name}**\n"
        f"Contact person: {lead_name}\n\n"
        f"{_build_data_context(data)}"
    )

    return system, user


# ── 2. Pain Points ────────────────────────────────────────────────────────────

def build_pain_points_prompt(
    lead_name: str,
    company_name: str,
    data: RawResearchData,
) -> tuple[str, str]:
    system = (
        "You are a sales intelligence specialist. Your job is to identify "
        "specific, evidence-backed business pain points for a company.\n\n"
        "Rules:\n"
        "- Every pain point MUST cite specific evidence from the data (exact quotes preferred).\n"
        "- Avoid generic pain points. 'They need more leads' is useless. "
        "'Their sales page shows no CRM integration and 3 manual steps' is useful.\n"
        "- severity must be: high, medium, or low.\n"
        "- revenue_impact must explain the specific cost of this pain (e.g., 'lost deals, "
        "wasted SDR hours, churn').\n"
        "- Return 2–5 pain points, most severe first.\n\n"
        "Respond ONLY with a JSON array:\n"
        "[\n"
        "  {\n"
        '    "title": "Short descriptive title",\n'
        '    "description": "Specific description with context",\n'
        '    "evidence": "Exact quote or observation from data",\n'
        '    "source": "website:about|linkedin|news|tech_stack",\n'
        '    "severity": "high|medium|low",\n'
        '    "revenue_impact": "How this costs them money"\n'
        "  }\n"
        "]"
    )

    user = (
        f"Find pain points for: **{company_name}**\n"
        f"Contact person: {lead_name}\n\n"
        f"{_build_data_context(data)}"
    )

    return system, user


# ── 3. Personal Profile ───────────────────────────────────────────────────────

def build_personal_profile_prompt(
    lead_name: str,
    title: str,
    company_name: str,
    data: RawResearchData,
) -> tuple[str, str]:
    system = (
        "You are an executive profiler for B2B sales. Your job is to build a "
        "profile of a decision-maker from their LinkedIn and public presence.\n\n"
        "Rules:\n"
        "- Focus on what makes this PERSON specifically reachable — not generic traits.\n"
        "- personal_hooks must be concrete referenceable details "
        "(e.g., 'spoke at SaaStr 2024', 'ex-Stripe', 'posts about AI frequently').\n"
        "- communication_style: formal | casual | technical\n"
        "- decision_making_power: high | medium | low\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        '  "background": "Professional history in 1–2 sentences",\n'
        '  "likely_priorities": ["priority 1", "priority 2", "priority 3"],\n'
        '  "communication_style": "formal|casual|technical",\n'
        '  "what_resonates": "What messaging style/topics land with them",\n'
        '  "personal_hooks": ["specific detail 1", "specific detail 2"],\n'
        '  "decision_making_power": "high|medium|low"\n'
        "}"
    )

    user = (
        f"Profile this person: **{lead_name}**, {title} at {company_name}\n\n"
        f"{_build_data_context(data)}"
    )

    return system, user


# ── 4. Email Angle ────────────────────────────────────────────────────────────

def build_email_angle_prompt(
    lead_name: str,
    company_name: str,
    company_profile_json: str,
    pain_points_json: str,
    personal_profile_json: str,
) -> tuple[str, str]:
    system = (
        "You are a cold email strategist. Given research on a lead, you determine "
        "the optimal outreach angle — hook, tone, CTA, and subject lines.\n\n"
        "Rules:\n"
        "- best_hook must reference something SPECIFIC to this person or company, "
        "not a generic compliment.\n"
        "- subject_lines: exactly 3 options, under 60 characters each, no clickbait.\n"
        "- recommended_cta: low-friction (15-min call, quick question, etc.).\n"
        "- tone: formal | conversational | direct\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        '  "best_hook": "The specific opening reference",\n'
        '  "pain_point_to_reference": "Which pain point to lead with",\n'
        '  "tone": "formal|conversational|direct",\n'
        '  "relevant_social_proof": "Type of proof they would trust",\n'
        '  "recommended_cta": "Low-friction action to request",\n'
        '  "subject_lines": ["line 1", "line 2", "line 3"],\n'
        '  "email_structure_recommendation": "Brief note on body structure"\n'
        "}"
    )

    user = (
        f"Determine outreach angle for: **{lead_name}** at **{company_name}**\n\n"
        f"## COMPANY PROFILE\n{company_profile_json}\n\n"
        f"## PAIN POINTS\n{pain_points_json}\n\n"
        f"## PERSONAL PROFILE\n{personal_profile_json}"
    )

    return system, user


# ── 5. Quality Score ──────────────────────────────────────────────────────────

def build_quality_score_prompt(
    lead_name: str,
    company_name: str,
    company_profile_json: str,
    pain_points_json: str,
    personal_profile_json: str,
    data_completeness: float,
) -> tuple[str, str]:
    system = (
        "You are a lead quality evaluator for a B2B outreach system. "
        "Score the overall quality and readiness of this lead for outreach.\n\n"
        "Scoring rubric (1–10 scale):\n"
        "  9–10: Strong fit + strong data + clear pain points + accessible decision-maker\n"
        "  7–8:  Good fit + adequate data + identifiable pain points\n"
        "  5–6:  Some fit + limited data OR unclear pain\n"
        "  3–4:  Poor fit OR very low data completeness\n"
        "  1–2:  No identifiable fit or contact path\n\n"
        f"Note: Data completeness for this lead is {data_completeness:.0%}.\n\n"
        "grade must be: A (8–10), B (6–7), C (4–5), D (1–3)\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        '  "score": 7.5,\n'
        '  "grade": "B",\n'
        '  "reasoning": "Why this score",\n'
        '  "strengths": ["strength 1", "strength 2"],\n'
        '  "weaknesses": ["concern 1", "concern 2"],\n'
        '  "recommended_approach": "How to pitch this specific person"\n'
        "}"
    )

    user = (
        f"Score lead: **{lead_name}** at **{company_name}**\n\n"
        f"## COMPANY PROFILE\n{company_profile_json}\n\n"
        f"## PAIN POINTS\n{pain_points_json}\n\n"
        f"## PERSONAL PROFILE\n{personal_profile_json}"
    )

    return system, user
