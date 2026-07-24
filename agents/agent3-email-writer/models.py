"""
Data models for Agent 3: Email Writer.

Four model groups mirror the four layers:
  - InputContext      → Layer 1 assembled context
  - EmailDraft        → Layer 2 writing output
  - QualityReport     → Layer 3 quality output
  - WrittenEmail      → Layer 4 final stored record (FK → Lead + ResearchProfile)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Layer 1: Input context ────────────────────────────────────────────────────

class EmailTemplate(BaseModel):
    """Structural guide for the email — not a fill-in-the-blank template."""

    name: str
    use_case: str          # "cold_outreach" | "pain_point_led" | "compliment_led" | "question_led"
    structure_guide: str   # describes the flow the writer should follow
    max_words: int = 180
    tone_hint: str = ""    # optional tone modifier beyond brand voice


class BrandVoice(BaseModel):
    """Defines how the sender communicates — enforced by the quality layer."""

    company_name: str
    tone: str                            # "professional" | "conversational" | "direct" | "warm"
    value_proposition: str               # what the company offers in one sentence
    key_messages: list[str] = []         # recurring themes to reinforce
    forbidden_phrases: list[str] = []    # never say these
    style_rules: list[str] = []          # grammar/style preferences
    example_good_sentence: str = ""      # reference for the right voice


class SenderProfile(BaseModel):
    """Who is writing the email."""

    first_name: str
    last_name: str
    title: str
    company: str
    email: str
    linkedin_url: str = ""
    phone: str = ""
    signature: str = ""                  # pre-formatted signature block

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class InputContext(BaseModel):
    """Assembled input passed to the Writing Layer for one lead."""

    lead_id: str
    research_profile_id: str

    # Lead identity (from Agent 1)
    lead_first_name: str
    lead_last_name: str
    lead_title: str
    lead_company: str

    # Research (from Agent 2) — only what the writers actually need
    company_summary: str = ""
    pain_points_summary: str = ""         # top 1-2 pain points as plain text
    personal_hooks: list[str] = []        # specific referenceable details
    recommended_hook: str = ""            # from email_angle.best_hook
    recommended_cta: str = ""             # from email_angle.recommended_cta
    recommended_tone: str = ""            # from email_angle.tone
    subject_line_ideas: list[str] = []    # from email_angle.subject_lines
    campaign_instruction: str = ""

    # Config
    template: EmailTemplate
    brand_voice: BrandVoice
    sender: SenderProfile


# ── Layer 2: Writing output ───────────────────────────────────────────────────

class EmailDraft(BaseModel):
    """Intermediate assembled draft — before quality checking."""

    subject: str
    hook: str            # opening line
    body: str            # bridge + pain + value prop
    cta: str             # call to action line
    full_body: str       # assembled: hook + "\n\n" + body + "\n\n" + cta + "\n\n" + signature
    word_count: int = 0


# ── Layer 3: Quality output ───────────────────────────────────────────────────

class QualityCheck(BaseModel):
    checker: str           # "personalization" | "spam" | "tone" | "length"
    passed: bool
    score: float           # 0.0 – 1.0
    issues: list[str] = []
    suggestions: list[str] = []


class QualityReport(BaseModel):
    checks: list[QualityCheck] = []
    overall_passed: bool = False
    overall_score: float = 0.0          # average across all checks
    revised_subject: Optional[str] = None   # set if subject was revised
    revised_body: Optional[str] = None      # set if body was revised after quality failure

    def compute_overall(self) -> None:
        if not self.checks:
            return
        self.overall_score = sum(c.score for c in self.checks) / len(self.checks)
        self.overall_passed = all(c.passed for c in self.checks)


# ── Layer 4: Final stored record ──────────────────────────────────────────────

class WrittenEmail(BaseModel):
    """
    The final email record stored in the email database.
    lead_id and research_profile_id are the FK chain back to Agents 1 and 2.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    lead_id: str                     # FK → Agent 1 Lead.id
    research_profile_id: str         # FK → Agent 2 ResearchProfile.id

    # Email content
    subject: str
    body: str                        # full assembled body including signature
    hook: str = ""
    cta: str = ""

    # Metadata
    lead_first_name: str = ""
    lead_last_name: str = ""
    lead_company: str = ""
    sender_name: str = ""
    sender_email: str = ""
    tone: str = ""
    template_name: str = ""

    # Quality
    quality_score: float = 0.0
    quality_passed: bool = False
    quality_report: Optional[QualityReport] = None

    # Pipeline state
    status: str = "draft"            # "draft" | "approved" | "queued" | "sent"
    job_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EmailJob(BaseModel):
    """Tracks one batch email-writing run."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    status: str = "pending"          # "pending" | "in_progress" | "complete"
    total: int = 0
    written: int = 0
    quality_passed: int = 0
    quality_failed: int = 0
    skipped: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
