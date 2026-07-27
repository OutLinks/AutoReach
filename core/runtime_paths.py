"""Runtime storage paths shared by the orchestrator and all agents."""

from __future__ import annotations

import json
import os
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def data_root() -> Path | None:
    """Return the configured persistent data root, if one was supplied."""
    value = os.getenv("AUTOREACH_DATA_DIR", "").strip()
    return Path(value).expanduser().resolve() if value else None


def configured_database_path() -> Path | None:
    """Return the UI-selected shared database without requiring a .env file."""
    direct = os.getenv("AUTOREACH_DATABASE_PATH", "").strip()
    if direct:
        return Path(direct).expanduser().resolve()
    root = data_root() or (_REPOSITORY_ROOT / ".data")
    selection = root / "api" / "database.json"
    if not selection.exists():
        return None
    try:
        payload = json.loads(selection.read_text(encoding="utf-8"))
        return Path(payload["path"]).expanduser().resolve()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def orchestrator_output_dir() -> Path:
    root = data_root()
    return root / "orchestrator" if root else _REPOSITORY_ROOT / "orchestrator" / "output"


def agent_output_dir(agent_directory: str) -> Path:
    root = data_root()
    return (
        root / "agents" / agent_directory
        if root
        else _REPOSITORY_ROOT / "agents" / agent_directory / "output"
    )


def api_output_dir() -> Path:
    root = data_root()
    return root / "api" if root else _REPOSITORY_ROOT / "api" / "output"
