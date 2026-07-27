"""Database-backed application configuration and first-run database selection."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SettingKind = Literal["text", "secret", "boolean", "integer"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    group: str
    kind: SettingKind
    default: str
    env_name: str | None = None
    help: str = ""
    minimum: int | None = None
    options: tuple[str, ...] = ()


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "simulate",
        "Simulation mode",
        "Pipeline",
        "boolean",
        "true",
        "AUTOREACH_SIMULATE",
        "Keep enabled until live providers and sending accounts are configured.",
    ),
    SettingSpec(
        "reply_handling_enabled",
        "Automated reply handling",
        "Pipeline",
        "boolean",
        "false",
        "AUTOREACH_REPLY_HANDLING_ENABLED",
    ),
    SettingSpec(
        "followup_after_days",
        "Follow up after (days)",
        "Pipeline",
        "integer",
        "3",
        "AUTOREACH_FOLLOWUP_AFTER_DAYS",
        minimum=1,
    ),
    SettingSpec(
        "leads_per_day",
        "Leads per day",
        "Pipeline",
        "integer",
        "50",
        "AUTOREACH_LEADS_PER_DAY",
        minimum=1,
    ),
    SettingSpec(
        "emails_per_day",
        "Emails per day",
        "Pipeline",
        "integer",
        "50",
        "AUTOREACH_EMAILS_PER_DAY",
        minimum=1,
    ),
    SettingSpec(
        "followups_per_day",
        "Follow-ups per day",
        "Pipeline",
        "integer",
        "100",
        "AUTOREACH_FOLLOWUPS_PER_DAY",
        minimum=1,
    ),
    SettingSpec(
        "daily_send_limit",
        "Daily send limit",
        "Pipeline",
        "integer",
        "50",
        "AUTOREACH_DAILY_SEND_LIMIT",
        minimum=1,
    ),
    SettingSpec(
        "hourly_send_limit",
        "Hourly send limit",
        "Pipeline",
        "integer",
        "10",
        "AUTOREACH_HOURLY_SEND_LIMIT",
        minimum=1,
    ),
    SettingSpec(
        "scheduler_enabled",
        "Automatic scheduler",
        "Scheduler",
        "boolean",
        "false",
        help="Runs the pipeline on its configured schedule.",
    ),
    SettingSpec(
        "scheduler_timezone",
        "Scheduler timezone",
        "Scheduler",
        "text",
        "UTC",
        help="IANA timezone, for example Asia/Colombo.",
    ),
    SettingSpec(
        "llm_provider",
        "LLM provider",
        "AI",
        "text",
        "anthropic",
        "AUTOREACH_LLM_PROVIDER",
        options=("anthropic", "openai", "openrouter"),
    ),
    SettingSpec(
        "llm_model",
        "LLM model",
        "AI",
        "text",
        "claude-sonnet-4-6",
        "AUTOREACH_LLM_MODEL",
    ),
    SettingSpec(
        "anthropic_api_key",
        "Anthropic API key",
        "AI",
        "secret",
        "",
        "ANTHROPIC_API_KEY",
    ),
    SettingSpec(
        "openai_api_key",
        "OpenAI API key",
        "AI",
        "secret",
        "",
        "OPENAI_API_KEY",
    ),
    SettingSpec(
        "openrouter_api_key",
        "OpenRouter API key",
        "AI",
        "secret",
        "",
        "OPENROUTER_API_KEY",
    ),
    SettingSpec(
        "firecrawl_api_key",
        "Firecrawl API key",
        "Lead data",
        "secret",
        "",
        "FIRECRAWL_API_KEY",
    ),
    SettingSpec(
        "google_places_api_key",
        "Google Places API key",
        "Lead data",
        "secret",
        "",
        "GOOGLE_PLACES_API_KEY",
    ),
    SettingSpec(
        "tavily_api_key",
        "Tavily API key",
        "Lead data",
        "secret",
        "",
        "TAVILY_API_KEY",
    ),
    SettingSpec(
        "hunter_api_key",
        "Hunter API key",
        "Lead data",
        "secret",
        "",
        "HUNTER_API_KEY",
    ),
    SettingSpec(
        "abstract_api_key",
        "Abstract API key",
        "Lead data",
        "secret",
        "",
        "ABSTRACT_API_KEY",
    ),
    SettingSpec(
        "gnews_api_key",
        "GNews API key",
        "Lead data",
        "secret",
        "",
        "GNEWS_API_KEY",
    ),
    SettingSpec(
        "github_api_key",
        "GitHub API key",
        "Lead data",
        "secret",
        "",
        "GITHUB_API_KEY",
    ),
    SettingSpec(
        "wappalyzer_api_key",
        "Wappalyzer API key",
        "Lead data",
        "secret",
        "",
        "WAPPALYZER_API_KEY",
    ),
    SettingSpec(
        "crunchbase_api_key",
        "Crunchbase API key",
        "Lead data",
        "secret",
        "",
        "CRUNCHBASE_API_KEY",
    ),
    SettingSpec(
        "whoisxml_api_key",
        "WhoisXML API key",
        "Lead data",
        "secret",
        "",
        "WHOISXML_API_KEY",
    ),
    SettingSpec(
        "securitytrails_api_key",
        "SecurityTrails API key",
        "Lead data",
        "secret",
        "",
        "SECURITYTRAILS_API_KEY",
    ),
    SettingSpec(
        "redis_url",
        "Redis queue URL",
        "Lead data",
        "text",
        "redis://localhost:6379",
        "REDIS_URL",
        "Optional working queue for lead discovery; durable results are copied to SQLite.",
    ),
    SettingSpec(
        "sender_provider",
        "Sending provider",
        "Sending",
        "text",
        "smtp",
        "AGENT4_PROVIDER",
        options=(
            "smtp",
            "gmail",
            "sendgrid",
            "mailgun",
            "postmark",
            "resend",
            "ses",
            "instantly",
            "outreach",
        ),
    ),
    SettingSpec(
        "sender_email",
        "Sender email",
        "Sending",
        "text",
        "",
        "SENDER_EMAIL",
    ),
    SettingSpec(
        "sender_name",
        "Sender name",
        "Sending",
        "text",
        "AutoReach",
        "SENDER_FROM_NAME",
    ),
    SettingSpec(
        "smtp_host",
        "SMTP host",
        "Sending",
        "text",
        "",
        "SMTP_HOST",
    ),
    SettingSpec(
        "smtp_port",
        "SMTP port",
        "Sending",
        "integer",
        "587",
        "SMTP_PORT",
        minimum=1,
    ),
    SettingSpec(
        "smtp_username",
        "SMTP username",
        "Sending",
        "text",
        "",
        "SMTP_USERNAME",
    ),
    SettingSpec(
        "smtp_password",
        "SMTP password",
        "Sending",
        "secret",
        "",
        "SMTP_PASSWORD",
    ),
    SettingSpec(
        "gmail_access_token",
        "Gmail access token",
        "Sending",
        "secret",
        "",
        "GMAIL_ACCESS_TOKEN",
    ),
    SettingSpec(
        "sendgrid_api_key",
        "SendGrid API key",
        "Sending",
        "secret",
        "",
        "SENDGRID_API_KEY",
    ),
    SettingSpec(
        "sendgrid_api_base",
        "SendGrid API base",
        "Sending",
        "text",
        "https://api.sendgrid.com",
        "SENDGRID_API_BASE",
    ),
    SettingSpec(
        "mailgun_api_key",
        "Mailgun API key",
        "Sending",
        "secret",
        "",
        "MAILGUN_API_KEY",
    ),
    SettingSpec(
        "mailgun_domain",
        "Mailgun domain",
        "Sending",
        "text",
        "",
        "MAILGUN_DOMAIN",
    ),
    SettingSpec(
        "mailgun_api_base",
        "Mailgun API base",
        "Sending",
        "text",
        "https://api.mailgun.net/v3",
        "MAILGUN_API_BASE",
    ),
    SettingSpec(
        "postmark_server_token",
        "Postmark server token",
        "Sending",
        "secret",
        "",
        "POSTMARK_SERVER_TOKEN",
    ),
    SettingSpec(
        "postmark_message_stream",
        "Postmark message stream",
        "Sending",
        "text",
        "outbound",
        "POSTMARK_MESSAGE_STREAM",
    ),
    SettingSpec(
        "resend_api_key",
        "Resend API key",
        "Sending",
        "secret",
        "",
        "RESEND_API_KEY",
    ),
    SettingSpec(
        "instantly_api_key",
        "Instantly API key",
        "Sending",
        "secret",
        "",
        "INSTANTLY_API_KEY",
    ),
    SettingSpec(
        "outreach_access_token",
        "Outreach access token",
        "Sending",
        "secret",
        "",
        "OUTREACH_ACCESS_TOKEN",
    ),
    SettingSpec(
        "aws_ses_region",
        "AWS SES region",
        "Sending",
        "text",
        "",
        "AWS_SES_REGION",
    ),
    SettingSpec(
        "aws_access_key_id",
        "AWS access key ID",
        "Sending",
        "secret",
        "",
        "AWS_ACCESS_KEY_ID",
    ),
    SettingSpec(
        "aws_secret_access_key",
        "AWS secret access key",
        "Sending",
        "secret",
        "",
        "AWS_SECRET_ACCESS_KEY",
    ),
    SettingSpec(
        "tracking_base_url",
        "Tracking base URL",
        "Sending",
        "text",
        "http://localhost:8000",
        "AGENT4_TRACKING_BASE_URL",
    ),
    SettingSpec(
        "calendly_link",
        "Calendly link",
        "Replies",
        "text",
        "https://calendly.com/autoreach/intro",
        "CALENDLY_LINK",
    ),
)

_SPEC_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        is_secret  INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatabaseLocator:
    """Stores only the selected database location; application data stays in SQLite."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.path = self.data_dir / "api" / "database.json"

    def selected_path(self) -> Path:
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return self.validate(payload["path"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        return (self.data_dir / "autoreach.db").resolve()

    def validate(self, value: str | Path) -> Path:
        text = str(value).strip()
        if not text or text == ":memory:":
            raise ValueError("Choose a persistent SQLite database file")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("Database filename must end in .db, .sqlite, or .sqlite3")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def save(self, value: str | Path) -> Path:
        selected = self.validate(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"engine": "sqlite", "path": str(selected)}, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        return selected


class ConfigStore:
    """Settings stored in the same database as the rest of AutoReach."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            for statement in _SCHEMA:
                self._conn.execute(statement)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @property
    def configured(self) -> bool:
        return bool(self._meta("configured_at"))

    def initialize(
        self,
        values: dict[str, Any] | None = None,
    ) -> None:
        if self.configured:
            raise ValueError("AutoReach has already been configured")
        with self._conn:
            self._set_meta("configured_at", _now())
        if values:
            self.update(values)

    def update(self, values: dict[str, Any]) -> None:
        normalized: dict[str, str] = {}
        for key, value in values.items():
            spec = _SPEC_BY_KEY.get(key)
            if spec is None:
                raise ValueError(f"Unknown setting: {key}")
            if spec.kind == "secret" and (value is None or str(value) == ""):
                continue
            normalized[key] = self._normalize(spec, value)

        with self._conn:
            for key, value in normalized.items():
                spec = _SPEC_BY_KEY[key]
                self._conn.execute(
                    """
                    INSERT INTO app_settings (key, value, is_secret, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        is_secret = excluded.is_secret,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, int(spec.kind == "secret"), _now()),
                )

    def get(self, key: str) -> str:
        spec = _SPEC_BY_KEY[key]
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else spec.default

    def get_bool(self, key: str) -> bool:
        return self.get(key) == "true"

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def public_settings(self) -> dict[str, Any]:
        items = []
        for spec in SETTING_SPECS:
            value = self.get(spec.key)
            items.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "group": spec.group,
                    "kind": spec.kind,
                    "value": "" if spec.kind == "secret" else self._public_value(spec, value),
                    "configured": bool(value) if spec.kind == "secret" else True,
                    "help": spec.help,
                    "minimum": spec.minimum,
                    "options": list(spec.options),
                }
            )
        return {"database_path": str(self.path), "items": items}

    def apply_to_process(self) -> None:
        """Expose DB values to legacy adapters without reading a .env file."""
        for spec in SETTING_SPECS:
            if spec.env_name:
                os.environ[spec.env_name] = self.get(spec.key)
        os.environ["AUTOREACH_DATABASE_PATH"] = str(self.path)
        os.environ["AGENT4_SIMULATE"] = self.get("simulate")
        os.environ["AGENT5_SIMULATE"] = self.get("simulate")
        os.environ["AGENT5_ENABLED"] = self.get("reply_handling_enabled")

    def close(self) -> None:
        self._conn.close()

    def _meta(self, key: str) -> str:
        row = self._conn.execute(
            "SELECT value FROM app_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO app_meta (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )

    @staticmethod
    def _normalize(spec: SettingSpec, value: Any) -> str:
        if spec.kind == "boolean":
            if isinstance(value, bool):
                return "true" if value else "false"
            text = str(value).strip().lower()
            if text not in {"true", "false"}:
                raise ValueError(f"{spec.label} must be true or false")
            return text
        if spec.kind == "integer":
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{spec.label} must be a whole number") from exc
            if spec.minimum is not None and number < spec.minimum:
                raise ValueError(f"{spec.label} must be at least {spec.minimum}")
            return str(number)
        text = str(value).strip()
        if spec.key == "scheduler_timezone":
            try:
                ZoneInfo(text)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(
                    "Scheduler timezone must be a valid IANA timezone"
                ) from exc
        if spec.options and text not in spec.options:
            raise ValueError(f"{spec.label} must be one of: {', '.join(spec.options)}")
        return text

    @staticmethod
    def _public_value(spec: SettingSpec, value: str) -> Any:
        if spec.kind == "boolean":
            return value == "true"
        if spec.kind == "integer":
            return int(value)
        return value
