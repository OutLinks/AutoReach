"""
Volume limiter (Scheduling Layer #3).

Enforces the sending caps that keep mailboxes out of spam folders:
  - per-account daily and hourly limits,
  - a per-minute burst ceiling,
  - a minimum gap between two emails to the *same recipient domain*.

In-process counters; the scheduler asks `can_send` before assigning a send to an
account and calls `record` once it's queued. Counters reset on day/hour rollover.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone

from ...config import ServiceConfig
from ...models import SendingAccount

logger = logging.getLogger(__name__)


class VolumeLimiter:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        # account_email → counters
        self._day_count: dict[str, int] = defaultdict(int)
        self._hour_count: dict[str, int] = defaultdict(int)
        self._day_key: dict[str, str] = {}
        self._hour_key: dict[str, str] = {}
        # recent send timestamps per account (for burst) and per domain (for spacing)
        self._minute_window: dict[str, deque] = defaultdict(deque)
        self._domain_last_sent: dict[str, datetime] = {}

    def can_send(
        self,
        account: SendingAccount,
        recipient: str,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). reason is empty when allowed."""
        now = now or datetime.now(timezone.utc)
        self._roll(account.email, now)

        if not account.is_sendable:
            return False, f"account {account.email} is {account.status}"

        if self._day_count[account.email] >= account.daily_limit:
            return False, f"daily limit reached ({account.daily_limit})"

        if self._hour_count[account.email] >= account.hourly_limit:
            return False, f"hourly limit reached ({account.hourly_limit})"

        # Burst: how many sends in the trailing 60s.
        window = self._minute_window[account.email]
        cutoff = now.timestamp() - 60
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._config.burst_per_minute:
            return False, f"burst limit reached ({self._config.burst_per_minute}/min)"

        # Same-domain spacing.
        domain = self._domain(recipient)
        last = self._domain_last_sent.get(domain)
        if last is not None:
            gap = (now - last).total_seconds()
            if gap < self._config.min_seconds_between_same_domain:
                return False, f"domain {domain} cooling down ({gap:.0f}s < min)"

        return True, ""

    def record(
        self,
        account: SendingAccount,
        recipient: str,
        now: datetime | None = None,
    ) -> None:
        """Register that a send was queued/sent against this account."""
        now = now or datetime.now(timezone.utc)
        self._roll(account.email, now)
        self._day_count[account.email] += 1
        self._hour_count[account.email] += 1
        self._minute_window[account.email].append(now.timestamp())
        self._domain_last_sent[self._domain(recipient)] = now
        # keep the model's counters in sync for persistence
        account.sent_today = self._day_count[account.email]
        account.sent_this_hour = self._hour_count[account.email]

    def remaining_today(self, account: SendingAccount, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        self._roll(account.email, now)
        return max(0, account.daily_limit - self._day_count[account.email])

    # ── Internal ───────────────────────────────────────────────────────────────

    def _roll(self, email: str, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        hour = now.strftime("%Y-%m-%d-%H")
        if self._day_key.get(email) != day:
            self._day_key[email] = day
            self._day_count[email] = 0
        if self._hour_key.get(email) != hour:
            self._hour_key[email] = hour
            self._hour_count[email] = 0

    @staticmethod
    def _domain(recipient: str) -> str:
        return recipient.split("@")[-1].lower() if "@" in recipient else recipient.lower()
