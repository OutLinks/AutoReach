"""
Warm-up manager (Scheduling Layer #4).

New mailboxes can't send at full volume without torching their reputation, so we
ramp the daily cap with age:

    days 0–13  → 20/day   (new)
    days 14–27 → 30/day   (warming)
    days 28–55 → 50/day   (established)
    days 56+   → 100/day  (premium)

The manager rewrites each account's effective `daily_limit` and flips its status
between "warming" and "active". Ramp values come from ServiceConfig.warmup_ramp.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...config import ServiceConfig
from ...models import SendingAccount

logger = logging.getLogger(__name__)


class WarmupManager:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    def apply(self, account: SendingAccount, now: datetime | None = None) -> SendingAccount:
        """Set the account's effective daily cap and warm/active status by age."""
        now = now or datetime.now(timezone.utc)

        if account.warmup_start_date is None:
            # Never warmed → treat as brand new starting now.
            account.warmup_start_date = now

        days_active = self._days_active(account, now)
        cap = self._config.warmup_daily_cap(days_active)
        account.daily_limit = min(account.daily_limit, cap) if account.daily_limit else cap
        account.daily_limit = cap

        if account.status != "paused":
            account.status = "warming" if days_active < self._config.warmup_days else "active"

        logger.debug(
            "WarmupManager: %s — %d days active → cap %d (%s)",
            account.email, days_active, cap, account.status,
        )
        return account

    def is_warming(self, account: SendingAccount, now: datetime | None = None) -> bool:
        return self._days_active(account, now or datetime.now(timezone.utc)) < self._config.warmup_days

    def _days_active(self, account: SendingAccount, now: datetime) -> int:
        start = account.warmup_start_date
        if start is None:
            return 0
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0, (now - start).days)
