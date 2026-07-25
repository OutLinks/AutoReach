"""Environment-backed settings for the HTTP backend."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.runtime_paths import api_output_dir, data_root


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppSettings:
    environment: str = "development"
    api_secret: str = ""
    data_dir: Path = field(default_factory=lambda: data_root() or api_output_dir().parent)
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 30
    scheduler_timezone: str = "UTC"
    cors_origins: tuple[str, ...] = ()

    @property
    def job_db_path(self) -> Path:
        return self.data_dir / "api" / "jobs.db"

    def validate(self) -> None:
        if self.environment == "production" and len(self.api_secret) < 24:
            raise ValueError("AUTOREACH_API_SECRET must contain at least 24 characters in production")
        if self.scheduler_interval_seconds < 5:
            raise ValueError("AUTOREACH_SCHEDULER_INTERVAL_SECONDS must be at least 5")
        try:
            ZoneInfo(self.scheduler_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"AUTOREACH_SCHEDULER_TIMEZONE is invalid: {self.scheduler_timezone}"
            ) from exc

    @classmethod
    def from_env(cls) -> "AppSettings":
        root = data_root() or api_output_dir().parent
        origins = tuple(
            item.strip()
            for item in os.getenv("AUTOREACH_CORS_ORIGINS", "").split(",")
            if item.strip()
        )
        settings = cls(
            environment=os.getenv("AUTOREACH_ENV", "development").strip().lower(),
            api_secret=os.getenv("AUTOREACH_API_SECRET", ""),
            data_dir=root,
            scheduler_enabled=_bool("AUTOREACH_SCHEDULER_ENABLED", False),
            scheduler_interval_seconds=int(
                os.getenv("AUTOREACH_SCHEDULER_INTERVAL_SECONDS", "30")
            ),
            scheduler_timezone=os.getenv("AUTOREACH_SCHEDULER_TIMEZONE", "UTC"),
            cors_origins=origins,
        )
        settings.validate()
        return settings
