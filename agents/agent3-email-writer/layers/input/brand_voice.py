"""
Brand voice loader.

Defines the default brand voice and supports loading a custom one
from a JSON file at a configured path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ...models import BrandVoice

logger = logging.getLogger(__name__)

_DEFAULT_BRAND_VOICE = BrandVoice(
    company_name="AutoReach",
    tone="conversational",
    value_proposition="AI-powered outreach that feels human — personalized emails at scale",
    key_messages=[
        "We help teams reach the right people with the right message",
        "Real personalization based on deep research, not mail merge",
        "Save hours of manual research and writing per lead",
    ],
    forbidden_phrases=[
        "synergy",
        "circle back",
        "touch base",
        "leverage",
        "paradigm shift",
        "game-changer",
        "disruptive",
        "best-in-class",
        "world-class",
        "I hope this email finds you well",
        "I wanted to reach out",
        "Quick question",
        "Just following up",
        "Not sure if you saw my last email",
    ],
    style_rules=[
        "Write like a smart person talking to another smart person",
        "Short sentences preferred over long complex ones",
        "No exclamation marks",
        "Use contractions (you're, we've, it's) to sound natural",
        "Avoid passive voice",
    ],
    example_good_sentence=(
        "Noticed you're scaling the sales team — "
        "most companies at that stage hit a wall with manual prospecting."
    ),
)


class BrandVoiceLoader:
    def __init__(self, voice_file: Optional[str] = None) -> None:
        self._voice_file = voice_file

    def load(self) -> BrandVoice:
        if not self._voice_file:
            return _DEFAULT_BRAND_VOICE

        path = Path(self._voice_file)
        if not path.exists():
            logger.warning(
                "BrandVoiceLoader: file not found at %s, using default", path
            )
            return _DEFAULT_BRAND_VOICE

        try:
            data = json.loads(path.read_text())
            return BrandVoice(**data)
        except Exception as exc:
            logger.error(
                "BrandVoiceLoader: failed to parse %s — %s, using default", path, exc
            )
            return _DEFAULT_BRAND_VOICE
