"""
Timezone detector (Scheduling Layer #1).

Infers an IANA timezone for a lead from whatever location signal is available
(explicit timezone, city/region/country strings). No network calls — a curated
lookup table covers the common business hubs; everything else falls back to the
configured default so a send time can always be computed.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# City / region / country keyword → IANA timezone. Lowercased substring match.
_LOCATION_TZ: dict[str, str] = {
    # US
    "san francisco": "America/Los_Angeles",
    "bay area": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "denver": "America/Denver",
    "colorado": "America/Denver",
    "chicago": "America/Chicago",
    "austin": "America/Chicago",
    "texas": "America/Chicago",
    "new york": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "atlanta": "America/New_York",
    # Intl
    "london": "Europe/London",
    "united kingdom": "Europe/London",
    "dublin": "Europe/Dublin",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid",
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "singapore": "Asia/Singapore",
    "sydney": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "bangalore": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "toronto": "America/Toronto",
    "canada": "America/Toronto",
}

# 2-letter country code → timezone (coarse, capital-city granularity).
_COUNTRY_TZ: dict[str, str] = {
    "us": "America/New_York",
    "gb": "Europe/London",
    "uk": "Europe/London",
    "de": "Europe/Berlin",
    "fr": "Europe/Paris",
    "jp": "Asia/Tokyo",
    "in": "Asia/Kolkata",
    "au": "Australia/Sydney",
    "ca": "America/Toronto",
    "sg": "Asia/Singapore",
}


class TimezoneDetector:
    def __init__(self, default_timezone: str = "UTC") -> None:
        self._default = default_timezone

    def detect(self, lead: dict) -> str:
        """Return the best-guess IANA timezone string for a lead."""
        # 1. Explicit timezone wins.
        explicit = lead.get("timezone") or lead.get("tz")
        if explicit:
            return explicit

        # 2. Free-text location fields.
        haystacks = [
            lead.get("location"),
            lead.get("city"),
            lead.get("region"),
            lead.get("state"),
            lead.get("company_location"),
        ]
        text = " ".join(str(h) for h in haystacks if h).lower()
        if text:
            for keyword, tz in _LOCATION_TZ.items():
                if keyword in text:
                    return tz

        # 3. Country code.
        country = (lead.get("country") or lead.get("country_code") or "").strip().lower()
        if country in _COUNTRY_TZ:
            return _COUNTRY_TZ[country]

        logger.debug(
            "TimezoneDetector: no signal for lead %s, using default %s",
            lead.get("id", "?"),
            self._default,
        )
        return self._default
