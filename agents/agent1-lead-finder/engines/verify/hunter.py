"""
Hunter.io email verification adapter.

Verifies whether an email address is safe to send to before spending
enrichment credits on invalid addresses.

API docs: https://hunter.io/api-documentation/v2#email-verifier
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"

# Map Hunter.io status strings to our normalised labels
_STATUS_MAP = {
    "valid": "valid",
    "invalid": "invalid",
    "accept_all": "catch-all",
    "webmail": "invalid",       # webmail = personal, treat as invalid for B2B
    "disposable": "invalid",
    "unknown": "unknown",
}

FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "protonmail.com", "mail.com",
})


class HunterAdapter:
    """Verifies email addresses via Hunter.io."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def verify(self, lead: Lead) -> Lead:
        """
        Verify `lead.email` and update verification fields in-place.
        Returns the updated lead (even if verification fails — fields stay None).
        """
        if not lead.email:
            lead.email_status = "unknown"
            return lead

        # Cheap local check first — free email domains are always invalid for B2B
        domain = lead.email.split("@")[-1].lower() if "@" in lead.email else ""
        if domain in FREE_EMAIL_DOMAINS:
            lead.email_status = "invalid"
            lead.email_score = 0.0
            logger.debug("Free email domain rejected: %s", lead.email)
            return lead

        data = await self._call_api(lead.email)
        if data is None:
            lead.email_status = "unknown"
            return lead

        raw_status = data.get("status", "unknown")
        lead.email_status = _STATUS_MAP.get(raw_status, "unknown")
        lead.email_score = float(data.get("score", 0))

        logger.debug(
            "Hunter verified %s → status=%s score=%.0f",
            lead.email, lead.email_status, lead.email_score or 0,
        )
        return lead

    async def _call_api(self, email: str) -> dict[str, Any] | None:
        params = {"email": email, "api_key": self._api_key}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.get(_VERIFY_URL, params=params)
                resp.raise_for_status()
                return (resp.json().get("data") or {})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Hunter.io rate limited")
                else:
                    logger.error("Hunter HTTP %s for %s", exc.response.status_code, email)
                return None
            except Exception as exc:
                logger.error("Hunter request failed for %s: %s", email, exc)
                return None
