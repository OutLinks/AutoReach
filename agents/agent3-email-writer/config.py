"""
Configuration for Agent 3: Email Writer.

Brand voice, sender profile, and quality thresholds are all set here.
This is the single place to configure how AutoReach sounds and who it comes from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.model_selection.types import ModelConfig


@dataclass
class ServiceConfig:
    # Tailored by the Orchestrator Campaign Planner for a single live run.
    campaign_instruction: str = ""

    # Model
    model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=4096,
            temperature=0.7,     # higher temperature for creative email writing
        )
    )

    # Database
    db_path: str = field(
        default_factory=lambda: str(
            Path(__file__).parent / "output" / "emails.db"
        )
    )

    # Concurrency
    concurrency: int = 5

    # Quality thresholds (0.0 – 1.0)
    quality_pass_threshold: float = 0.70

    # Which quality checks to run
    run_personalization_check: bool = True
    run_spam_check: bool = True
    run_tone_check: bool = True
    run_length_check: bool = True

    # Email length limits (word count of the body, not subject)
    min_words: int = 60
    max_words: int = 200

    # How many times to attempt a revision if quality fails (0 = no revision)
    max_revision_attempts: int = 1

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls()
