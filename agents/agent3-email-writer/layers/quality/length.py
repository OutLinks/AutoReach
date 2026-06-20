"""
Email length checker.

Rule-based. Cold emails should be 60–200 words. Longer emails get
dramatically lower open rates and replies.
"""

from __future__ import annotations

from ...config import ServiceConfig
from ...models import QualityCheck


def check(full_body: str, config: ServiceConfig) -> QualityCheck:
    word_count = len(full_body.split())
    char_count = len(full_body)

    issues: list[str] = []
    suggestions: list[str] = []

    if word_count < config.min_words:
        issues.append(
            f"Email is too short: {word_count} words (minimum {config.min_words})"
        )
        suggestions.append("Add more context about the specific value you bring.")

    if word_count > config.max_words:
        issues.append(
            f"Email is too long: {word_count} words (maximum {config.max_words})"
        )
        suggestions.append(
            "Cut the body to the most essential 1–2 points. "
            "Remove any restating of facts the lead already knows."
        )

    reading_time_sec = round(word_count / 3.5)   # average reading speed ~210 wpm
    if reading_time_sec > 60:
        issues.append(
            f"Reading time is {reading_time_sec}s — cold emails should be under 60s"
        )

    score = 1.0
    if issues:
        # Penalise proportionally to how far outside bounds we are
        overage = max(0, word_count - config.max_words)
        shortage = max(0, config.min_words - word_count)
        deviation = (overage + shortage) / config.max_words
        score = max(0.0, 1.0 - deviation)

    return QualityCheck(
        checker="length",
        passed=len(issues) == 0,
        score=score,
        issues=issues,
        suggestions=suggestions,
    )
