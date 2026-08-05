"""
Configuration for the Orchestrator.

Mirrors the architecture doc's "Configuration Management" section — targeting,
volume, quality, escalation, reputation — plus the operational knobs the engine
needs (schedule, concurrency, retry/backoff). This is the single control panel
for how the whole factory runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.model_selection import ModelConfig, model_config_from_env
from core.runtime_paths import orchestrator_output_dir


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return max(minimum, int(value))


@dataclass
class TargetingConfig:
    industries: list[str] = field(default_factory=lambda: ["real estate", "property management"])
    company_sizes: list[str] = field(default_factory=lambda: ["5-20", "21-50"])
    locations: list[str] = field(default_factory=lambda: ["San Francisco", "Bay Area"])
    job_titles: list[str] = field(default_factory=lambda: ["founder", "ceo", "owner"])
    # The default prompt Agent 1 receives when the Orchestrator triggers a search.
    search_prompt: str = (
        "Find real estate and property management companies in the San Francisco "
        "Bay Area with 5-50 employees; target founders, CEOs, and owners."
    )


@dataclass
class VolumeConfig:
    leads_per_day: int = 50
    emails_per_day: int = 50
    followups_per_day: int = 100
    max_concurrent_research: int = 5     # Agent 2
    max_concurrent_writing: int = 10     # Agent 3


@dataclass
class QualityConfig:
    min_email_score: float = 0.70        # Agent 3 quality (0–1) to send
    min_research_completeness: float = 0.80
    min_lead_quality: float = 0.60       # 6/10 → 0.6 to enter outreach


@dataclass
class EscalationConfig:
    low_confidence: float = 0.70         # Agent 5 classification floor
    high_value_lead: float = 0.80        # normalized score (8/10)
    bounce_rate_pause: float = 0.05
    complaint_rate_pause: float = 0.001


@dataclass
class ReputationConfig:
    daily_send_limit: int = 50
    hourly_send_limit: int = 10
    warmup_days: int = 14
    bounce_rate_threshold: float = 0.05
    complaint_rate_threshold: float = 0.001


@dataclass
class RetryConfig:
    # Transient-error backoff schedule (seconds), then dead-letter.
    backoff_seconds: tuple[int, ...] = (30, 120, 600)
    max_attempts: int = 3
    # Circuit breaker: open after N consecutive stage failures; cooldown before
    # a single half-open trial.
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: int = 300
    # A lead sitting "in progress" longer than this is treated as a stuck timeout.
    stage_timeout_minutes: int = 30
    # A lead waiting in any queue longer than this raises a red flag.
    max_queue_wait_hours: int = 24


@dataclass
class ScheduleConfig:
    """Daily plan — hour (local 24h) → list of stages to run."""

    plan: dict[int, list[str]] = field(default_factory=lambda: {
        6: ["health_check"],
        7: ["find"],
        8: ["research"],
        9: ["write", "send"],
        18: ["followup"],
        20: ["report"],
    })


@dataclass
class OrchestratorConfig:
    db_path: str = field(
        default_factory=lambda: str(orchestrator_output_dir() / "orchestrator.db")
    )

    targeting: TargetingConfig = field(default_factory=TargetingConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    escalation: EscalationConfig = field(default_factory=EscalationConfig)
    reputation: ReputationConfig = field(default_factory=ReputationConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    # One planner call compiles a user's request into a reusable CampaignBrief.
    campaign_model: ModelConfig = field(default_factory=lambda: model_config_from_env(
        max_tokens=4096,
        temperature=0.1,
    ))
    # Natural-language operator requests use the LLM as a bounded tool router;
    # the state machine remains the authority for lifecycle execution.
    orchestrator_model: ModelConfig = field(default_factory=lambda: model_config_from_env(
        max_tokens=4096,
        temperature=0.1,
    ))
    llm_orchestrator_enabled: bool = False

    # When True, the engine uses simulated adapters (no real agents / APIs run).
    simulate: bool = True
    # Agent 5 is not part of the MVP. Enable only when reply ingestion,
    # automated reply drafting, and human-handoff workflows are ready.
    reply_handling_enabled: bool = False
    # Follow-up cadence threshold: a SENT lead with no reply after this many days
    # enters the follow-up stage.
    followup_after_days: int = 3

    # Per-stage concurrency caps (Agent 1 is a batch op → effectively unlimited).
    @property
    def concurrency(self) -> dict[str, int]:
        return {
            "find": 1,
            "research": self.volume.max_concurrent_research,
            "write": self.volume.max_concurrent_writing,
            "send": max(1, self.reputation.hourly_send_limit),
            "followup": max(1, self.reputation.hourly_send_limit),
            "reply": 3 if self.reply_handling_enabled else 0,
        }

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        config = cls()
        config.simulate = _env_bool("AUTOREACH_SIMULATE", True)
        config.llm_orchestrator_enabled = _env_bool(
            "AUTOREACH_LLM_ORCHESTRATOR_ENABLED", not config.simulate
        )
        config.reply_handling_enabled = _env_bool("AUTOREACH_REPLY_HANDLING_ENABLED", False)
        config.followup_after_days = _env_int(
            "AUTOREACH_FOLLOWUP_AFTER_DAYS", config.followup_after_days
        )
        config.volume.leads_per_day = _env_int(
            "AUTOREACH_LEADS_PER_DAY", config.volume.leads_per_day
        )
        config.volume.emails_per_day = _env_int(
            "AUTOREACH_EMAILS_PER_DAY", config.volume.emails_per_day
        )
        config.volume.followups_per_day = _env_int(
            "AUTOREACH_FOLLOWUPS_PER_DAY", config.volume.followups_per_day
        )
        config.reputation.daily_send_limit = _env_int(
            "AUTOREACH_DAILY_SEND_LIMIT", config.reputation.daily_send_limit
        )
        config.reputation.hourly_send_limit = _env_int(
            "AUTOREACH_HOURLY_SEND_LIMIT", config.reputation.hourly_send_limit
        )
        return config
