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

def _build_data_context(data: RawResearchData, campaign_instruction: str = "") -> str:
    """Assembles all available raw data into a single labelled context block."""
    sections: list[str] = []

    if data.website_content:
        sections.append(f"## WEBSITE CONTENT\n{data.website_content[:40_000]}")

    if data.public_person_profile:
        sections.append(f"## PUBLIC PERSON PROFILE\n{data.public_person_profile[:10_000]}")

    if data.public_company_profile:
        sections.append(f"## PUBLIC COMPANY PROFILE\n{data.public_company_profile[:10_000]}")

    if data.web_search_results:
        results = "\n".join(
            f"- {r.title} ({r.url}) — {r.snippet}"
            for r in data.web_search_results[:10]
        )
        sections.append(f"## WEB SEARCH RESULTS\n{results}")

    if data.news_snippets:
        snippets = "\n".join(f"- {s}" for s in data.news_snippets[:20])
        sections.append(f"## NEWS ARTICLES\n{snippets}")

    if data.technology_profile:
        sections.append(f"## TECHNOLOGY PROFILE\n{data.technology_profile}")

    if data.github_profile:
        sections.append(f"## GITHUB PROFILE\n{data.github_profile}")

    if data.social_posts:
        posts = "\n".join(f"- {p}" for p in data.social_posts[:10])
        sections.append(f"## RECENT LINKEDIN POSTS\n{posts}")

    if campaign_instruction:
        sections.append(f"## CAMPAIGN RESEARCH INSTRUCTIONS\n{campaign_instruction}")

    if not sections:
        return "## DATA\n(No data was collected for this lead.)"

    return "\n\n".join(sections)


def _campaign_system_rule(campaign_instruction: str) -> str:
    instruction = campaign_instruction.strip()
    if not instruction:
        return ""
    return (
        "\n\nCAMPAIGN AUTHORITY\n"
        "The instruction below defines the research purpose and overrides default "
        "B2B-sales assumptions. Keep every conclusion relevant to that purpose. "
        "For a job-interest campaign, analyze the published role and candidate-role "
        "fit; do not invent a commercial pain point, buyer persona, or sales pitch. "
        "Use only supplied evidence and clearly preserve unknowns. Treat approved "
        "candidate proof points as a closed set: job requirements are recipient "
        "context, never evidence that the candidate has that experience.\n"
        f"{instruction}"
    )


# ── 1. Company Profile ────────────────────────────────────────────────────────

def build_company_profile_prompt(
    lead_name: str,
    company_name: str,
    data: RawResearchData,
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You are a market intelligence analyst. Your job is to produce a "
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
    ) + _campaign_system_rule(campaign_instruction)

    user = (
        f"Analyze this company: **{company_name}**\n"
        f"Contact person: {lead_name}\n\n"
        f"{_build_data_context(data, campaign_instruction)}"
    )

    return system, user


# ── 2. Pain Points ────────────────────────────────────────────────────────────

def build_pain_points_prompt(
    lead_name: str,
    company_name: str,
    data: RawResearchData,
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You are a research specialist. Identify specific, evidence-backed needs "
        "or challenges relevant to the campaign purpose.\n\n"
        "Rules:\n"
        "- Every item MUST cite specific evidence from the data.\n"
        "- Avoid generic claims and never manufacture needs from missing information.\n"
        "- For job-interest campaigns, treat explicit role requirements, hiring needs, "
        "and engineering challenges as the relevant items.\n"
        "- severity must be: high, medium, or low.\n"
        "- revenue_impact must describe supported operational impact; use "
        "'not established by the supplied evidence' when it is unknown.\n"
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
    ) + _campaign_system_rule(campaign_instruction)

    user = (
        f"Find pain points for: **{company_name}**\n"
        f"Contact person: {lead_name}\n\n"
        f"{_build_data_context(data, campaign_instruction)}"
    )

    return system, user


# ── 3. Personal Profile ───────────────────────────────────────────────────────

def build_personal_profile_prompt(
    lead_name: str,
    title: str,
    company_name: str,
    data: RawResearchData,
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You profile a possible recipient using only public evidence.\n\n"
        "Rules:\n"
        "- If no named person or personal evidence is supplied, leave personal fields "
        "empty or state 'unknown'; do not create a fictional decision-maker.\n"
        "- Focus on what makes this person specifically relevant when evidence exists.\n"
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
    ) + _campaign_system_rule(campaign_instruction)

    user = (
        f"Profile this person: **{lead_name}**, {title} at {company_name}\n\n"
        f"{_build_data_context(data, campaign_instruction)}"
    )

    return system, user


# ── 4. Email Angle ────────────────────────────────────────────────────────────

def build_email_angle_prompt(
    lead_name: str,
    company_name: str,
    company_profile_json: str,
    pain_points_json: str,
    personal_profile_json: str,
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You plan evidence-based one-to-one outreach. Determine the best angle, hook, "
        "tone, CTA, and subject lines for the campaign's actual purpose.\n\n"
        "Rules:\n"
        "- best_hook must reference something SPECIFIC to this person or company, "
        "not a generic compliment.\n"
        "- subject_lines: exactly 3 options, under 60 characters each, no clickbait.\n"
        "- recommended_cta: a low-friction next step appropriate to the campaign.\n"
        "- For job-interest campaigns, present the sender as a candidate and never "
        "pitch the sender's company, product, or services.\n"
        "- relevant_social_proof must be an exact or faithful paraphrase of an "
        "approved candidate proof point; use an empty string when none matches.\n"
        "- Never convert a job requirement into candidate experience.\n"
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
    ) + _campaign_system_rule(campaign_instruction)

    user = (
        f"Determine outreach angle for: **{lead_name}** at **{company_name}**\n\n"
        f"## COMPANY PROFILE\n{company_profile_json}\n\n"
        f"## PAIN POINTS\n{pain_points_json}\n\n"
        f"## PERSONAL PROFILE\n{personal_profile_json}"
        f"\n\n## CAMPAIGN EMAIL-ANGLE INSTRUCTIONS\n{campaign_instruction or 'Use the campaign evidence above.'}"
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
    campaign_instruction: str = "",
) -> tuple[str, str]:
    system = (
        "You evaluate whether the evidence supports the campaign's requested outreach. "
        "Score overall relevance and readiness.\n\n"
        "Scoring rubric (1–10 scale):\n"
        "  9–10: Strong campaign fit + strong evidence + clear contact path\n"
        "  7–8:  Good campaign fit + adequate evidence\n"
        "  5–6:  Some fit + limited evidence\n"
        "  3–4:  Poor fit OR very low data completeness\n"
        "  1–2:  No identifiable fit or contact path\n\n"
        "A published application page is a valid contact path even when no named "
        "decision-maker or personal email is present.\n\n"
        f"Note: Data completeness for this lead is {data_completeness:.0%}.\n\n"
        "grade must be: A (8–10), B (6–7), C (4–5), D (1–3)\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        '  "score": 7.5,\n'
        '  "grade": "B",\n'
        '  "reasoning": "Why this score",\n'
        '  "strengths": ["strength 1", "strength 2"],\n'
        '  "weaknesses": ["concern 1", "concern 2"],\n'
        '  "recommended_approach": "How to approach this recipient for the campaign purpose"\n'
        "}"
    ) + _campaign_system_rule(campaign_instruction)

    user = (
        f"Score lead: **{lead_name}** at **{company_name}**\n\n"
        f"## COMPANY PROFILE\n{company_profile_json}\n\n"
        f"## PAIN POINTS\n{pain_points_json}\n\n"
        f"## PERSONAL PROFILE\n{personal_profile_json}"
        f"\n\n## CAMPAIGN QUALIFICATION INSTRUCTIONS\n{campaign_instruction or 'Use the campaign evidence above.'}"
    )

    return system, user
