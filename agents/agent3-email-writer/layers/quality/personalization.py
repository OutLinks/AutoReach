"""
Personalization checker.

LLM-based. Verifies the email references real, specific details about the
lead — not just their name and company. A generic email that could be sent
to 500 people at once should fail this check.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import InputContext, QualityCheck
from ...prompt import build_personalization_check_prompt

logger = logging.getLogger(__name__)

_PASS_SCORE = 6.0   # out of 10 from the LLM → mapped to 0.6 threshold


def _rule_based_fallback(full_email: str, ctx: InputContext) -> QualityCheck:
    """Fast rule-based fallback if the LLM call fails."""
    text_lower = full_email.lower()
    issues: list[str] = []

    if ctx.lead_first_name.lower() not in text_lower:
        issues.append("Lead's first name is not mentioned.")

    if ctx.lead_company.lower() not in text_lower:
        issues.append("Company name is not mentioned in the body.")

    score = 1.0 - (len(issues) * 0.4)
    return QualityCheck(
        checker="personalization",
        passed=len(issues) == 0,
        score=max(0.0, score),
        issues=issues,
        suggestions=["Reference the lead by name and mention their company specifically."]
        if issues
        else [],
    )


async def check(full_email: str, ctx: InputContext, config: ServiceConfig) -> QualityCheck:
    if not config.run_personalization_check:
        return QualityCheck(checker="personalization", passed=True, score=1.0)

    system, user = build_personalization_check_prompt(
        full_email=full_email,
        lead_name=f"{ctx.lead_first_name} {ctx.lead_last_name}",
        company_name=ctx.lead_company,
        pain_points_summary=ctx.pain_points_summary,
        personal_hooks=ctx.personal_hooks,
    )

    try:
        adapter = get_model(config.model)
        response = await adapter.complete(
            messages=[Message(role="user", content=user)],
            system=system,
        )
        raw = (response.content or "{}").strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```"))

        data = json.loads(raw)
        llm_score = float(data.get("score", 5)) / 10.0   # normalize 0-10 → 0-1
        passed = float(data.get("score", 0)) >= _PASS_SCORE
        issues = data.get("issues") or []
        suggestions = data.get("suggestions") or []

        return QualityCheck(
            checker="personalization",
            passed=passed,
            score=llm_score,
            issues=issues,
            suggestions=suggestions,
        )

    except Exception as exc:
        logger.warning("PersonalizationChecker: LLM call failed — %s, using fallback", exc)
        return _rule_based_fallback(full_email, ctx)
