"""Local environment file loading."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(filename: str = ".env") -> None:
    """
    Load simple KEY=VALUE entries from a local env file without overriding
    variables already present in the process environment.
    """
    path = _find_env_file(filename)
    if path is None:
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _find_env_file(filename: str) -> Path | None:
    cwd = Path.cwd().resolve()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None
