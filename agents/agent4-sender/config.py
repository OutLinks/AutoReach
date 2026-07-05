"""
Configuration for Agent 4: Sender + Follow-Up Manager.

Controls scheduling windows, volume governance, sending provider, tracking,
sequence cadence, and reputation thresholds. This is the single place to tune
how AutoReach delivers mail and protects sender reputation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.model_selection.types import ModelConfig


@dataclass
class ServiceConfig:
    # Model — used by the sequence layer to write follow-up copy.
    model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=2048,
            temperature=0.7,
        )
    )

    # Database (Agent 4's own send/tracking store)
    db_path: str = field(
        default_factory=lambda: str(Path(__file__).parent / "output" / "sends.db")
    )

    # Where Agent 3 wrote its emails (the source of what we send)
    emails_db_path: str = field(
        default_factory=lambda: str(
            Path(__file__).parent.parent / "agent3-email-writer" / "output" / "emails.db"
        )
    )

    # ── Layer 1: Scheduling ────────────────────────────────────────────────────
    # Local hour (24h) at which to deliver. Doc: "10:00 AM in their local timezone".
    target_send_hour: int = 10
    # Business-hours window — sends are clamped into [start, end).
    business_hour_start: int = 9
    business_hour_end: int = 17
    # Weekdays to avoid (0=Mon … 6=Sun). Doc: avoid Mon, Fri, weekend.
    avoid_weekdays: tuple[int, ...] = (0, 4, 5, 6)
    default_timezone: str = "UTC"

    # Volume governance (per sending account)
    daily_send_limit: int = 50
    hourly_send_limit: int = 10
    burst_per_minute: int = 3
    min_seconds_between_same_domain: int = 30

    # Warm-up ramp (days_since_start → daily cap). Doc volume table.
    warmup_days: int = 14
    warmup_ramp: tuple[tuple[int, int], ...] = (
        (0, 20),    # weeks 1–2: 20/day
        (14, 30),   # weeks 3–4: 30/day
        (28, 50),   # established: 50/day
        (56, 100),  # premium: 100/day
    )

    # ── Layer 2: Sending ───────────────────────────────────────────────────────
    provider: str = "smtp"           # "instantly" | "gmail" | "outreach" | "ses" | "smtp"
    # When true, providers don't hit any real API — they simulate delivery so the
    # whole pipeline runs end-to-end without credentials (mirrors Agent 3 fallbacks).
    simulate: bool = True
    concurrency: int = 5

    # ── Layer 3: Tracking ──────────────────────────────────────────────────────
    track_opens: bool = True
    track_clicks: bool = True
    tracking_pixel_base_url: str = "https://track.autoreach.local"

    # ── Layer 4: Sequence ──────────────────────────────────────────────────────
    # Generate follow-up copy with the LLM; falls back to templates on failure.
    use_llm_followups: bool = True

    # ── Layer 5: Reputation ────────────────────────────────────────────────────
    bounce_rate_warning: float = 0.02
    bounce_rate_critical: float = 0.05
    complaint_rate_warning: float = 0.001
    complaint_rate_critical: float = 0.003
    open_rate_warning: float = 0.15
    soft_bounce_max_retries: int = 3

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        cfg = cls()
        cfg.provider = os.getenv("AGENT4_PROVIDER", cfg.provider)
        if os.getenv("AGENT4_SIMULATE"):
            cfg.simulate = os.getenv("AGENT4_SIMULATE", "true").lower() != "false"
        return cfg

    # ── Helpers ────────────────────────────────────────────────────────────────

    def warmup_daily_cap(self, days_active: int) -> int:
        """Return the daily send cap for an account that has been active N days."""
        cap = self.warmup_ramp[0][1]
        for day_threshold, value in self.warmup_ramp:
            if days_active >= day_threshold:
                cap = value
        return cap
