"""
Spam-trap detector (Reputation Layer #3).

Spam traps are addresses that never opt in and exist only to catch senders with
poor list hygiene — hitting one tanks reputation. There's no perfect signal
without sending, so we screen *before* sending using heuristics:

  - role/distribution addresses (info@, admin@, postmaster@, abuse@…),
  - known disposable / honeypot domains,
  - pristine traps recycled from dead domains (no MX is a weak proxy here, so we
    flag obviously malformed or suspicious local parts).

A flagged address is suppressed with reason "spam_trap" and never sent to.
"""

from __future__ import annotations

import logging
import re

from ...models import SuppressionEntry
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)

_ROLE_LOCALPARTS: set[str] = {
    "info", "admin", "administrator", "postmaster", "abuse", "spam",
    "noreply", "no-reply", "donotreply", "webmaster", "hostmaster",
    "root", "sysadmin", "support", "sales", "contact", "marketing",
}

_DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "trashmail.com", "yopmail.com", "sharklasers.com", "spam4.me",
}

_SUSPICIOUS_LOCAL = re.compile(r"^[a-z]{16,}$|^\d{6,}$")


class SpamTrapDetector:
    def __init__(self, store: SendStore) -> None:
        self._store = store

    def is_trap(self, email: str) -> tuple[bool, str]:
        """Return (flagged, reason). reason is empty when clean."""
        email = (email or "").strip().lower()
        if "@" not in email:
            return True, "malformed address"

        local, _, domain = email.partition("@")

        if local in _ROLE_LOCALPARTS:
            return True, f"role address '{local}@'"
        if domain in _DISPOSABLE_DOMAINS:
            return True, f"disposable domain '{domain}'"
        if _SUSPICIOUS_LOCAL.match(local):
            return True, "suspicious local part pattern"

        return False, ""

    def screen(self, email: str) -> bool:
        """Check + auto-suppress if flagged. Returns True if it's a trap."""
        flagged, reason = self.is_trap(email)
        if flagged:
            self._store.add_suppression(
                SuppressionEntry(value=email.lower(), reason="spam_trap", detail=reason)
            )
            logger.info("SpamTrapDetector: flagged %s (%s)", email, reason)
        return flagged
