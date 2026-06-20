"""
Output validator.

Checks a completed ResearchProfile for minimum quality thresholds before
it gets written to storage. Returns a (is_valid, reason) tuple.
"""

from __future__ import annotations

from ...models import ResearchProfile


_MIN_COMPLETENESS = 0.25    # at least one data source must have succeeded
_MIN_PAIN_POINTS = 1        # at least one pain point is required


def validate(profile: ResearchProfile) -> tuple[bool, str]:
    """
    Returns (True, "") for valid profiles.
    Returns (False, reason) for profiles that should be dropped.
    """
    if profile.status == "failed":
        return False, "profile status is 'failed'"

    if profile.research_completeness < _MIN_COMPLETENESS:
        return False, (
            f"data completeness {profile.research_completeness:.0%} "
            f"is below threshold {_MIN_COMPLETENESS:.0%}"
        )

    if not profile.company_profile:
        return False, "company profile analysis was not produced"

    if len(profile.pain_points) < _MIN_PAIN_POINTS:
        return False, f"requires at least {_MIN_PAIN_POINTS} pain point(s)"

    return True, ""
