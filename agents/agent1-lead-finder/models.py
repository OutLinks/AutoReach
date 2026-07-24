"""
Data models for Agent 1: Lead Finder.

All layers (search, verify, enrich, score) operate on the same Lead model,
progressively filling in fields as the pipeline advances.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class SearchCriteria(BaseModel):
    """
    Structured search parameters extracted from the user's prompt by the LLM.
    Every field is optional — the LLM fills only what the prompt specifies.
    """

    industries: list[str] = []
    company_sizes: list[str] = []       # e.g. ["5-20", "21-50"]
    locations: list[str] = []            # e.g. ["San Francisco", "Bay Area"]
    job_titles: list[str] = []           # e.g. ["CEO", "Founder", "Owner"]
    keywords: list[str] = []
    technologies: list[str] = []         # filter by tech stack
    # Public company or directory pages to scrape. URLs in the user's prompt
    # are preferred; configured seed URLs are added by SearchEngine.
    source_urls: list[str] = []
    max_results: int = 50
    # LLM suggests which enabled APIs to prioritize for this specific search
    api_priorities: list[str] = []
    reasoning: str = ""                  # LLM's explanation of its search strategy


class Lead(BaseModel):
    """
    A single lead that moves through all pipeline stages.

    Fields are populated progressively:
      - stage="raw"       → identity + company from search APIs
      - stage="deduped"   → is_duplicate confirmed
      - stage="verified"  → email_status, email_score, website_reachable filled
      - stage="enriched"  → LinkedIn, tech stack, funding, revenue filled
      - stage="scored"    → lead_score, lead_grade, score_breakdown filled
      - stage="complete"  → written to the persistent database
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    # ── Identity ──────────────────────────────────────────────────────────────
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    # ── Company ───────────────────────────────────────────────────────────────
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    company_domain: Optional[str] = None        # stripped domain, e.g. "acme.com"
    company_size: Optional[str] = None          # "1-10", "11-50", "51-200", "201-500", "500+"
    employee_count: Optional[int] = None
    industry: Optional[str] = None
    founded_year: Optional[int] = None
    company_description: Optional[str] = None
    annual_revenue: Optional[str] = None
    funding_total: Optional[str] = None
    funding_stage: Optional[str] = None         # "seed", "series_a", "series_b", etc.

    # ── Location ──────────────────────────────────────────────────────────────
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None

    # ── Professional ──────────────────────────────────────────────────────────
    title: Optional[str] = None
    seniority: Optional[str] = None             # "c_suite", "vp", "director", "manager"
    department: Optional[str] = None

    # ── Social ────────────────────────────────────────────────────────────────
    linkedin_url: Optional[str] = None
    company_linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None

    # ── Tech stack ────────────────────────────────────────────────────────────
    technologies: list[str] = []

    # ── Verification (filled by VerifyEngine) ─────────────────────────────────
    email_status: Optional[str] = None          # "valid", "invalid", "catch-all", "unknown"
    email_score: Optional[float] = None         # 0–100
    website_reachable: Optional[bool] = None

    # ── Scoring (filled by ScoreEngine) ───────────────────────────────────────
    lead_score: Optional[float] = None          # 0–100
    lead_grade: Optional[str] = None            # "A", "B", "C", "D"
    score_breakdown: dict[str, float] = {}

    # ── Metadata ──────────────────────────────────────────────────────────────
    sources: list[str] = []                     # which APIs contributed data
    raw_data: dict[str, Any] = {}
    is_duplicate: bool = False
    stage: str = "raw"                          # pipeline stage tracker
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    def merge_source(self, source: str) -> None:
        if source not in self.sources:
            self.sources.append(source)


class SearchJob(BaseModel):
    """Tracks the state and metrics of one end-to-end lead finding run."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    prompt: str
    criteria: Optional[SearchCriteria] = None
    status: str = "pending"     # "pending"|"searching"|"verifying"|"enriching"|"scoring"|"complete"|"failed"

    # Pipeline counters
    total_found: int = 0
    total_unique: int = 0
    total_verified: int = 0
    total_enriched: int = 0
    total_scored: int = 0
    total_written: int = 0

    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
