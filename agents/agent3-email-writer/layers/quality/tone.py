"""
Tone checker.

Rule-based. Verifies the email follows the brand voice:
- No forbidden phrases
- No style rule violations
- Tone indicators match (e.g. "conversational" → contractions present)
"""

from __future__ import annotations

from ...models import BrandVoice, QualityCheck

_FORMAL_INDICATORS = [
    "I am writing to", "I would like to", "Please do not hesitate",
    "Kindly", "I trust this", "At your earliest convenience",
    "I remain", "Yours sincerely", "Yours faithfully",
]

_PASSIVE_PATTERNS = [
    " is being ", " was being ", " have been ", " has been ",
    " will be ", " would be ",
]


def check(full_email: str, brand_voice: BrandVoice) -> QualityCheck:
    text_lower = full_email.lower()
    issues: list[str] = []
    suggestions: list[str] = []

    # Check forbidden phrases
    for phrase in brand_voice.forbidden_phrases:
        if phrase.lower() in text_lower:
            issues.append(f"Forbidden phrase used: '{phrase}'")
            suggestions.append(f"Replace '{phrase}' with something more natural.")

    # Tone-specific checks
    tone = brand_voice.tone.lower()

    if tone in ("conversational", "warm"):
        # Should use contractions
        formal_phrases = [p for p in _FORMAL_INDICATORS if p.lower() in text_lower]
        if formal_phrases:
            issues.append(
                f"Tone is '{tone}' but email uses formal phrases: "
                + ", ".join(f"'{p}'" for p in formal_phrases[:2])
            )
            suggestions.append("Use contractions and casual phrasing instead of formal language.")

    if tone == "direct":
        # Should be short and punchy — flag passive voice
        passive = [p for p in _PASSIVE_PATTERNS if p in text_lower]
        if passive:
            issues.append("Direct tone should avoid passive voice.")
            suggestions.append("Rewrite passive constructions as active sentences.")

    if tone == "professional":
        # Should NOT use overly casual phrases
        too_casual = ["hey", "yo ", "awesome", "super ", "totally"]
        casual_found = [p for p in too_casual if p in text_lower]
        if casual_found:
            issues.append(
                f"Professional tone should avoid overly casual words: "
                + ", ".join(f"'{p.strip()}'" for p in casual_found)
            )

    score = max(0.0, 1.0 - (len(issues) * 0.2))
    passed = len(issues) == 0

    return QualityCheck(
        checker="tone",
        passed=passed,
        score=score,
        issues=issues,
        suggestions=suggestions,
    )
