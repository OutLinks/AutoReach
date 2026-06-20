"""
Data models for Agent 2: Research Analyst.

Three model groups mirror the three layers:
  - RawResearchData   → Layer 1 output (scraped content)
  - Analysis models   → Layer 2 output (AI-extracted insights)
  - ResearchProfile   → Layer 3 output (final stored record, FK → Lead)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Layer 1: Raw scraped data ─────────────────────────────────────────────────

class NewsArticle(BaseModel):
    title: str
    snippet: str
    url: str
    date: str = ""
    source: str = ""


class RawResearchData(BaseModel):
    """
    All raw data collected by the Data Collection Layer for one lead.
    Passed directly into the AI Analysis Layer.
    """

    # Website (keys = page slug: "homepage", "about", "services", etc.)
    website_pages: dict[str, str] = {}

    # LinkedIn (ProxyCurl — raw markdown text of profiles)
    linkedin_person: Optional[str] = None
    linkedin_company: Optional[str] = None

    # News (SerpAPI / Google)
    news_articles: list[NewsArticle] = []

    # Social presence
    social_profiles: dict[str, Optional[str]] = {}   # platform → profile URL or None
    social_posts: list[str] = []                      # recent LinkedIn post texts

    # Which scrapers actually ran
    sources_attempted: list[str] = []
    sources_succeeded: list[str] = []

    # ── Computed helpers used by the Analysis layer ───────────────────────────

    @property
    def website_content(self) -> str:
        """All scraped website pages concatenated with section headers."""
        parts = [
            f"=== {page.upper()} PAGE ===\n{content}"
            for page, content in self.website_pages.items()
            if content and content.strip()
        ]
        return "\n\n".join(parts) if parts else ""

    @property
    def news_snippets(self) -> list[str]:
        return [
            f"[{a.date}] {a.title} — {a.snippet}" if a.date
            else f"{a.title} — {a.snippet}"
            for a in self.news_articles
        ]

    @property
    def completeness(self) -> float:
        """0–1 fraction of data categories that returned results."""
        checks = [
            bool(self.website_pages),
            bool(self.linkedin_person),
            bool(self.linkedin_company),
            bool(self.news_articles),
        ]
        return sum(checks) / len(checks)


# ── Layer 2: AI Analysis outputs ──────────────────────────────────────────────

class CompanyProfile(BaseModel):
    """What the company does, who it serves, where it's headed."""

    summary: str                     # 2–3 sentence description
    target_customer: str             # who their customers are
    business_model: str              # how they make money
    growth_stage: str                # "startup" | "scaling" | "established"
    competitive_advantage: str       # what makes them different
    recent_events: list[str] = []    # funding, expansion, product launches, hires
    market_position: str = ""        # how they sit relative to competitors


class PainPoint(BaseModel):
    """
    A specific, evidence-backed business pain point.
    Vague pain points are useless — every field must be specific.
    """

    title: str              # "Lead follow-up delays" not "lead management issues"
    description: str        # Specific, citing company size, tools, context
    evidence: str           # Exact quote or observation from scraped data
    source: str             # "website:about" | "linkedin" | "news" | "tech_stack"
    severity: str           # "high" | "medium" | "low"
    revenue_impact: str     # How this pain point costs them money


class PersonalProfile(BaseModel):
    """Profile of the individual decision-maker, not the company."""

    background: str                  # Professional history in 1–2 sentences
    likely_priorities: list[str]     # 3 things they care about most
    communication_style: str         # "formal" | "casual" | "technical"
    what_resonates: str              # What messaging would land with this person
    personal_hooks: list[str]        # Specific referenceable details from their profile
    decision_making_power: str       # "high" | "medium" | "low"


class EmailAngle(BaseModel):
    """The optimal outreach strategy, unique to this lead."""

    best_hook: str                   # Opening reference — must be specific to them
    pain_point_to_reference: str     # Which pain point to lead with
    tone: str                        # "formal" | "conversational" | "direct"
    relevant_social_proof: str       # Type of proof they'd find compelling
    recommended_cta: str             # Low-friction action to ask for
    subject_lines: list[str]         # 3 subject line options
    email_structure_recommendation: str  # Brief note on email body structure


class QualityScore(BaseModel):
    """Lead quality score with transparent reasoning."""

    score: float            # 1–10
    grade: str              # "A" | "B" | "C" | "D"
    reasoning: str          # Why this score
    strengths: list[str]    # Why this is a good lead
    weaknesses: list[str]   # Concerns or missing data
    recommended_approach: str   # How to pitch this specific person


# ── Layer 3: Final output record ──────────────────────────────────────────────

class ResearchProfile(BaseModel):
    """
    The complete research output for one lead.

    `lead_id` is the foreign key linking this record back to the Lead
    from Agent 1's database. All analysis fields are optional so the
    record can be written even when some analyses fail.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    lead_id: str                            # FK → Lead.id (Agent 1)

    # Layer 1 output
    raw_data: RawResearchData = Field(default_factory=RawResearchData)

    # Layer 2 outputs (None = that analysis was skipped or failed)
    company_profile: Optional[CompanyProfile] = None
    pain_points: list[PainPoint] = []
    personal_profile: Optional[PersonalProfile] = None
    email_angle: Optional[EmailAngle] = None
    quality_score: Optional[QualityScore] = None

    # Derived stats
    research_completeness: float = 0.0      # 0–1 from raw_data.completeness
    sources_used: list[str] = []

    # Pipeline state
    status: str = "pending"                 # "pending"|"in_progress"|"complete"|"partial"|"failed"
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    def mark_complete(self) -> None:
        self.status = "complete"
        self.completed_at = datetime.utcnow()

    def mark_partial(self, reason: str) -> None:
        self.status = "partial"
        self.error = reason
        self.completed_at = datetime.utcnow()


class ResearchJob(BaseModel):
    """Tracks one batch research run across multiple leads."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    lead_ids: list[str] = []
    status: str = "pending"
    total: int = 0
    completed: int = 0
    partial: int = 0
    failed: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        if not self.total:
            return 0.0
        return (self.completed + self.partial) / self.total
