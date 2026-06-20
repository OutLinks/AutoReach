"""
Spam trigger word checker.

Rule-based. No LLM needed. Checks against a curated list of words and
phrases that commonly cause emails to land in spam or feel salesy.
"""

from __future__ import annotations

import re

from ...models import QualityCheck

_SPAM_WORDS: list[str] = [
    "free", "guaranteed", "no obligation", "no risk", "no cost",
    "limited time", "act now", "don't miss out", "last chance", "urgent",
    "winner", "congratulations", "you've been selected", "claim your",
    "special promotion", "special offer", "double your", "earn extra",
    "extra income", "financial freedom", "make money", "100%",
    "amazing", "incredible", "unbelievable", "miracle", "revolutionary",
    "click here", "click below", "visit our website", "buy now", "order now",
    "dear friend", "dear sir", "dear madam",
    "!!!",
]

_SPAM_PATTERNS: list[str] = [
    r"\$\d+",          # dollar amounts
    r"\d+%\s+off",     # percent off
    r"[A-Z]{5,}",      # ALL CAPS WORDS (5+ chars)
]

# Minimum threshold to pass (fraction of checks passed)
_PASS_THRESHOLD = 0.85


def check(full_email: str) -> QualityCheck:
    text_lower = full_email.lower()
    issues: list[str] = []

    for word in _SPAM_WORDS:
        if word.lower() in text_lower:
            issues.append(f"Spam trigger word: '{word}'")

    for pattern in _SPAM_PATTERNS:
        if re.search(pattern, full_email):
            issues.append(f"Spam pattern matched: {pattern}")

    exclamation_count = full_email.count("!")
    if exclamation_count > 0:
        issues.append(f"Contains {exclamation_count} exclamation mark(s)")

    total_checks = len(_SPAM_WORDS) + len(_SPAM_PATTERNS) + 1
    passed_checks = total_checks - len(issues)
    score = max(0.0, passed_checks / total_checks)
    passed = len(issues) == 0

    return QualityCheck(
        checker="spam",
        passed=passed,
        score=score,
        issues=issues,
        suggestions=[
            "Remove or rephrase the flagged words/phrases."
        ] if issues else [],
    )
