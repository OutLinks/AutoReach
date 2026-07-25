"""
Lead scoring logic.

Scores each lead 0–100 based on data quality and fit signals.
Assigns a letter grade (A/B/C/D) and a per-factor breakdown
for transparency and debugging.

Scoring matrix (from the architecture doc):
  Company size fit          0–20
  Email quality             0–20
  Job title relevance       0–20
  Website presence          0–15
  Technology signals        0–15
  Recent activity           0–10
  Free email domain penalty –15

Grades: A = 80+, B = 60–79, C = 40–59, D = <40
"""

from __future__ import annotations

import logging

from ...models import Lead

logger = logging.getLogger(__name__)

# ── Title scoring ────────────────────────────────────────────────────────────

_TITLE_SCORES = {
    # Executive / decision maker
    "founder": 20, "co-founder": 20, "ceo": 20, "chief executive": 20,
    "owner": 18, "president": 18, "managing director": 18,
    "coo": 16, "cto": 16, "cmo": 16, "cfo": 16,
    # VP level
    "vp": 14, "vice president": 14, "svp": 14, "evp": 14,
    # Director level
    "director": 12, "head of": 12,
    # Manager
    "manager": 8, "lead": 6,
}

_FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "protonmail.com", "mail.com", "live.com",
})

_PREFERRED_SIZES = {"5-20", "11-50", "21-50", "51-200"}     # sweet spot for outreach
_LARGE_SIZES = {"201-500", "500+"}


class LeadScorer:
    """Scores leads based on data completeness and fit signals."""

    def score(self, lead: Lead) -> Lead:
        """Compute and set `lead_score`, `lead_grade`, and `score_breakdown`."""
        breakdown: dict[str, float] = {}

        breakdown["company_size"] = self._score_company_size(lead)
        breakdown["email_quality"] = self._score_email(lead)
        breakdown["title_relevance"] = self._score_title(lead)
        breakdown["website_presence"] = self._score_website(lead)
        breakdown["tech_signals"] = self._score_tech(lead)
        breakdown["data_completeness"] = self._score_completeness(lead)

        total = sum(breakdown.values())
        total = max(0.0, min(100.0, total))

        lead.lead_score = round(total, 1)
        lead.lead_grade = self._grade(total)
        lead.score_breakdown = breakdown
        lead.stage = "scored"
        lead.touch()
        return lead

    # ── Sub-scorers ──────────────────────────────────────────────────────────

    @staticmethod
    def _score_company_size(lead: Lead) -> float:
        size = lead.company_size or ""
        if size in _PREFERRED_SIZES:
            return 20.0
        if size in _LARGE_SIZES:
            return 10.0
        if size == "1-10":
            return 8.0
        if lead.employee_count:
            return 5.0
        return 0.0

    @staticmethod
    def _score_email(lead: Lead) -> float:
        if not lead.email:
            return 0.0
        # Penalty for free email domains
        domain = lead.email.split("@")[-1].lower() if "@" in lead.email else ""
        if domain in _FREE_EMAIL_DOMAINS:
            return -15.0

        status = lead.email_status or "unknown"
        score_map = {"valid": 20.0, "catch-all": 12.0, "unknown": 5.0, "invalid": -5.0}
        base = score_map.get(status, 5.0)

        # Boost for high verifier confidence
        if lead.email_score and lead.email_score >= 80:
            base = min(base + 5, 20.0)

        return base

    @staticmethod
    def _score_title(lead: Lead) -> float:
        title = (lead.title or "").lower()
        for keyword, pts in _TITLE_SCORES.items():
            if keyword in title:
                return float(pts)
        return 2.0 if title else 0.0

    @staticmethod
    def _score_website(lead: Lead) -> float:
        if lead.website_reachable is True:
            return 15.0
        if lead.company_website:
            return 8.0
        if lead.company_domain:
            return 5.0
        return 0.0

    @staticmethod
    def _score_tech(lead: Lead) -> float:
        if lead.technologies:
            # More tech signals = better data quality
            count = len(lead.technologies)
            return min(15.0, count * 2.5)
        return 0.0

    @staticmethod
    def _score_completeness(lead: Lead) -> float:
        """Bonus for data completeness — reward leads with full profiles."""
        fields = [
            lead.first_name, lead.last_name, lead.email, lead.title,
            lead.company_name, lead.linkedin_url, lead.city,
        ]
        filled = sum(1 for f in fields if f)
        return round((filled / len(fields)) * 10, 1)

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 80:
            return "A"
        if score >= 60:
            return "B"
        if score >= 40:
            return "C"
        return "D"
