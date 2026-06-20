"""
Email template library.

Templates are structural guides — they define the flow and tone modifier,
not the actual words. The Writing Layer fills them in with LLM-generated content.
"""

from __future__ import annotations

from ...models import EmailTemplate, BrandVoice, InputContext

# ── Built-in templates ────────────────────────────────────────────────────────

_TEMPLATES: dict[str, EmailTemplate] = {
    "cold_outreach": EmailTemplate(
        name="cold_outreach",
        use_case="cold_outreach",
        structure_guide=(
            "1. Hook: specific observation about their company or role. "
            "2. Intro: one sentence on who you are and what you do. "
            "3. Connection: tie their situation to what you help with. "
            "4. Value: one concrete result or outcome you can drive for them. "
            "5. CTA: low-friction next step."
        ),
        max_words=180,
        tone_hint="",
    ),
    "pain_point_led": EmailTemplate(
        name="pain_point_led",
        use_case="pain_point_led",
        structure_guide=(
            "1. Hook: name a specific operational pain you identified about their company. "
            "2. Intro: briefly who you are. "
            "3. Evidence: cite one specific observation that shows the pain is real. "
            "4. Solution: explain precisely how you address this — not generically. "
            "5. CTA: ask if it's worth a quick conversation."
        ),
        max_words=180,
        tone_hint="direct",
    ),
    "compliment_led": EmailTemplate(
        name="compliment_led",
        use_case="compliment_led",
        structure_guide=(
            "1. Hook: acknowledge a specific achievement, post, or initiative — be precise. "
            "2. Bridge: connect that achievement to a challenge it likely creates or reveals. "
            "3. Intro: who you are and the specific thing you help with. "
            "4. Value: how you've helped companies at a similar stage. "
            "5. CTA: propose a very light next step."
        ),
        max_words=170,
        tone_hint="warm",
    ),
    "question_led": EmailTemplate(
        name="question_led",
        use_case="question_led",
        structure_guide=(
            "1. Hook: a sharp, specific question about their situation — shows you've done homework. "
            "2. Context: briefly explain why you're asking (what you noticed). "
            "3. Intro: one sentence on who you are. "
            "4. Value: what you do if the answer to the question is a problem. "
            "5. CTA: invite them to answer the question or schedule a call."
        ),
        max_words=160,
        tone_hint="conversational",
    ),
}


class TemplateLibrary:
    def get(self, name: str) -> EmailTemplate:
        return _TEMPLATES.get(name, _TEMPLATES["cold_outreach"])

    def select_for_lead(
        self,
        research_profile: dict,
        brand_voice: BrandVoice,
    ) -> EmailTemplate:
        """
        Picks the most appropriate template based on available research data.
        Priority: pain_point_led > compliment_led > question_led > cold_outreach
        """
        pain_points = research_profile.get("pain_points") or []
        high_severity = [p for p in pain_points if p.get("severity") == "high"]

        personal_profile = research_profile.get("personal_profile") or {}
        personal_hooks = personal_profile.get("personal_hooks") or []

        quality_score = (research_profile.get("quality_score") or {}).get("score", 0)

        if high_severity:
            return _TEMPLATES["pain_point_led"]

        if len(personal_hooks) >= 2:
            return _TEMPLATES["compliment_led"]

        if quality_score and quality_score < 6.5:
            return _TEMPLATES["question_led"]

        return _TEMPLATES["cold_outreach"]

    def list_templates(self) -> list[str]:
        return list(_TEMPLATES.keys())
