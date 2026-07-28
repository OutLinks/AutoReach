"""
Sender profile loader.

Defines who is writing the emails. Loaded from environment variables
or a JSON file. No sender profile = cannot write emails.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from ...models import SenderProfile

logger = logging.getLogger(__name__)


class SenderProfileLoader:
    def __init__(self, profile_file: Optional[str] = None) -> None:
        self._profile_file = profile_file

    def load(self) -> SenderProfile:
        """
        Load order:
        1. JSON file (if configured)
        2. Environment variables (SENDER_FIRST_NAME, SENDER_LAST_NAME, etc.)
        3. Raise ValueError — sender is required
        """
        if self._profile_file:
            profile = self._from_file(self._profile_file)
            if profile:
                return profile

        profile = self._from_env()
        if profile:
            return profile

        raise ValueError(
            "Sender profile not configured. Set sender_first_name, "
            "sender_last_name, and sender_email with PATCH /v1/settings. "
            "Standalone Agent 3 may instead use the equivalent SENDER_* "
            "environment variables or a JSON profile file."
        )

    def _from_file(self, path_str: str) -> Optional[SenderProfile]:
        path = Path(path_str)
        if not path.exists():
            logger.warning("SenderProfileLoader: file not found at %s", path)
            return None
        try:
            data = json.loads(path.read_text())
            return SenderProfile(**data)
        except Exception as exc:
            logger.error("SenderProfileLoader: failed to parse %s — %s", path, exc)
            return None

    @staticmethod
    def _from_env() -> Optional[SenderProfile]:
        first = os.getenv("SENDER_FIRST_NAME", "")
        last = os.getenv("SENDER_LAST_NAME", "")
        title = os.getenv("SENDER_TITLE", "")
        company = os.getenv("SENDER_COMPANY", "")
        email = os.getenv("SENDER_EMAIL", "")

        if not all([first, last, email]):
            return None

        default_signature = "\n".join(
            part for part in (f"{first} {last}", title, company) if part
        )
        signature = os.getenv("SENDER_SIGNATURE") or default_signature

        return SenderProfile(
            first_name=first,
            last_name=last,
            title=title,
            company=company,
            email=email,
            linkedin_url=os.getenv("SENDER_LINKEDIN_URL", ""),
            phone=os.getenv("SENDER_PHONE", ""),
            signature=signature,
        )
