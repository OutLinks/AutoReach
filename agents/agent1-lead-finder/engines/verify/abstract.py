"""
Abstract Email Validation adapter.

Verifies deliverability after discovery/enrichment has produced an email.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...models import Lead

logger = logging.getLogger(__name__)

_VERIFY_URL = "https://emailvalidation.abstractapi.com/v1/"

FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "protonmail.com", "mail.com",
})


class AbstractEmailValidationAdapter:
    """Verifies email addresses via Abstract API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def verify(self, lead: Lead) -> Lead:
        if not lead.email:
            lead.email_status = "unknown"
            return lead

        domain = lead.email.split("@")[-1].lower() if "@" in lead.email else ""
        if domain in FREE_EMAIL_DOMAINS:
            lead.email_status = "invalid"
            lead.email_score = 0.0
            return lead

        data = await self._call_api(lead.email)
        if data is None:
            lead.email_status = "unknown"
            return lead

        lead.email_status = self._status(data)
        lead.email_score = self._score(data)
        lead.raw_data["abstract_email_validation"] = data
        lead.merge_source("abstract")

        logger.debug(
            "Abstract verified %s status=%s score=%.0f",
            lead.email, lead.email_status, lead.email_score or 0,
        )
        return lead

    async def _call_api(self, email: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.get(
                    _VERIFY_URL,
                    params={"api_key": self._api_key, "email": email},
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Abstract Email Validation rate limited")
                else:
                    logger.error("Abstract HTTP %s for %s", exc.response.status_code, email)
                return None
            except Exception as exc:
                logger.error("Abstract request failed for %s: %s", email, exc)
                return None

    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        deliverability = (data.get("deliverability") or "").upper()
        quality = float(data.get("quality_score") or 0)
        is_valid_format = (data.get("is_valid_format") or {}).get("value")
        is_disposable = (data.get("is_disposable_email") or {}).get("value")
        is_mx_found = (data.get("is_mx_found") or {}).get("value")
        is_smtp_valid = (data.get("is_smtp_valid") or {}).get("value")
        is_catch_all = (data.get("is_catchall_email") or {}).get("value")

        if is_disposable or is_valid_format is False or is_mx_found is False:
            return "invalid"
        if is_catch_all:
            return "catch-all"
        if deliverability == "DELIVERABLE" or is_smtp_valid is True or quality >= 0.8:
            return "valid"
        if deliverability == "UNDELIVERABLE" or is_smtp_valid is False:
            return "invalid"
        return "unknown"

    @staticmethod
    def _score(data: dict[str, Any]) -> float:
        quality = data.get("quality_score")
        if quality is None:
            return 0.0
        try:
            return round(float(quality) * 100, 1)
        except (TypeError, ValueError):
            return 0.0
