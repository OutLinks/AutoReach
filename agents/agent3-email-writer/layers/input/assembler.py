"""
Input Layer orchestrator.

Takes raw (research_profile, lead) dicts from Agents 1 & 2 and assembles
a clean InputContext for the Writing Layer. All the messy dict traversal
lives here so the Writing Layer gets a clean, typed object.
"""

from __future__ import annotations

import logging

from ...models import BrandVoice, InputContext, SenderProfile
from .template_library import TemplateLibrary

logger = logging.getLogger(__name__)


class InputAssembler:
    def __init__(self, brand_voice: BrandVoice, sender: SenderProfile) -> None:
        self._voice = brand_voice
        self._sender = sender
        self._templates = TemplateLibrary()

    def assemble(
        self,
        research_profile: dict,
        lead: dict,
        campaign_instruction: str = "",
    ) -> InputContext:
        """
        Assembles an InputContext from Agent 1 + Agent 2 dicts.
        Never raises — missing fields become empty strings.
        """
        template = self._templates.select_for_lead(research_profile, self._voice)

        # Pain points — take top 2, prefer high severity
        pain_points = sorted(
            research_profile.get("pain_points") or [],
            key=lambda p: {"high": 0, "medium": 1, "low": 2}.get(p.get("severity", ""), 3),
        )
        pain_summary = _format_pain_points(pain_points[:2])

        # Personal profile
        personal = research_profile.get("personal_profile") or {}
        personal_hooks = personal.get("personal_hooks") or []

        # Email angle from Agent 2
        email_angle = research_profile.get("email_angle") or {}

        # Company profile
        company_profile = research_profile.get("company_profile") or {}
        company_summary = company_profile.get("summary", "") or ""
        raw_data = research_profile.get("raw_data") or {}
        website_pages = raw_data.get("website_pages") or {}
        source_evidence = "\n".join(
            str(content) for content in website_pages.values() if content
        )

        return InputContext(
            lead_id=lead.get("id", ""),
            research_profile_id=research_profile.get("id", ""),
            lead_first_name=lead.get("first_name") or _split_name(lead)[0],
            lead_last_name=lead.get("last_name") or _split_name(lead)[1],
            lead_title=lead.get("title") or "",
            lead_company=lead.get("company_name") or "",
            company_summary=company_summary,
            source_evidence=source_evidence[:20_000],
            pain_points_summary=pain_summary,
            personal_hooks=personal_hooks[:5],
            recommended_hook=email_angle.get("best_hook") or "",
            recommended_cta=email_angle.get("recommended_cta") or "",
            recommended_tone=email_angle.get("tone") or self._voice.tone,
            subject_line_ideas=email_angle.get("subject_lines") or [],
            campaign_instruction=campaign_instruction,
            template=template,
            brand_voice=self._voice,
            sender=self._sender,
        )


def _format_pain_points(pain_points: list[dict]) -> str:
    if not pain_points:
        return ""
    lines = []
    for p in pain_points:
        title = p.get("title", "")
        desc = p.get("description", "")
        evidence = p.get("evidence", "")
        line = title
        if desc:
            line += f": {desc}"
        if evidence:
            line += f" (Evidence: {evidence[:150]})"
        lines.append(line)
    return "\n".join(lines)


def _split_name(lead: dict) -> tuple[str, str]:
    full = lead.get("full_name") or ""
    parts = full.strip().split(" ", 1) if full else ["", ""]
    return parts[0], parts[1] if len(parts) > 1 else ""
