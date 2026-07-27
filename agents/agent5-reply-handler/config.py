"""
Configuration for Agent 5: Reply Handler.

Controls where replies are read from, the classification model, escalation
thresholds (when to hand off to a human), and how responses are sent. This is
the single place to tune how autonomous the agent is allowed to be.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.model_selection.types import ModelConfig, model_config_from_env
from core.runtime_paths import agent_output_dir, configured_database_path


@dataclass
class ServiceConfig:
    # Agent 5 is opt-in for the MVP. Set AGENT5_ENABLED=true or construct a
    # config with enabled=True only after reply automation is ready.
    enabled: bool = False
    # Reserved for the reviewed campaign brief when reply handling is enabled.
    campaign_instruction: str = ""

    # Model — used for intent classification and reply generation.
    model: ModelConfig = field(
        default_factory=lambda: model_config_from_env(
            max_tokens=2048,
            temperature=0.4,     # lower temp: classification needs to be steady
        )
    )

    # Agent 5's own conversation store.
    db_path: str = field(
        default_factory=lambda: str(
            configured_database_path()
            or (agent_output_dir("agent5-reply-handler") / "conversations.db")
        )
    )

    # Where Agent 4 drops reply hand-off files.
    replies_dir: str = field(
        default_factory=lambda: str(agent_output_dir("agent4-sender") / "replies")
    )

    # Agent 3's emails DB — for the original email content (conversation context).
    emails_db_path: str = field(
        default_factory=lambda: str(
            configured_database_path()
            or (agent_output_dir("agent3-email-writer") / "emails.db")
        )
    )

    # Where to drop "stop sequence" signals Agent 4 can pick up.
    sequence_signals_dir: str = field(
        default_factory=lambda: str(agent_output_dir("agent4-sender") / "signals")
    )

    concurrency: int = 3                 # replies need care — keep low (doc)

    # ── Meeting booking ────────────────────────────────────────────────────────
    calendly_link: str = "https://calendly.com/autoreach/intro"

    # ── Escalation thresholds (architecture doc) ───────────────────────────────
    min_confidence_to_auto_handle: float = 0.70   # < 70% → human
    high_value_lead_score: float = 8.0            # score > 8 → human
    max_auto_exchanges: int = 3                   # > 3 exchanges → human
    escalate_on_negative_sentiment: bool = True   # angry/upset → human
    escalate_keywords: tuple[str, ...] = (
        "pricing", "discount", "contract", "legal", "lawsuit", "refund",
    )

    # ── Sending responses ──────────────────────────────────────────────────────
    # When True, replies aren't actually emailed — they're drafted, persisted, and
    # logged so the full pipeline runs without credentials (mirrors Agent 4).
    simulate: bool = True
    send_provider: str = "smtp"          # "smtp" | "gmail"
    sender_email: str = field(default_factory=lambda: os.getenv("SENDER_EMAIL", ""))
    sender_name: str = field(default_factory=lambda: os.getenv("SENDER_FROM_NAME", "AutoReach"))

    # Use the LLM for reply drafting; falls back to templates on failure.
    use_llm_replies: bool = True

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        cfg = cls()
        if os.getenv("AGENT5_ENABLED"):
            cfg.enabled = os.getenv("AGENT5_ENABLED", "false").lower() == "true"
        if os.getenv("AGENT5_SIMULATE"):
            cfg.simulate = os.getenv("AGENT5_SIMULATE", "true").lower() != "false"
        if os.getenv("CALENDLY_LINK"):
            cfg.calendly_link = os.getenv("CALENDLY_LINK", cfg.calendly_link)
        return cfg
