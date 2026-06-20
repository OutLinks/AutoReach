"""
Scheduling Layer orchestrator.

Ties the four scheduling components together to turn a sendable email + lead into
a concrete ScheduledSend:

  1. timezone_detector   → which timezone is this lead in?
  2. warmup_manager      → what's each account allowed to send today?
  3. volume_limiter      → which account still has headroom (round-robin + health)?
  4. send_time_optimizer → when, in UTC, should it go out?

Account selection is health-weighted round-robin: sort sendable accounts by
health (desc) then by load (asc), and pick the first one that passes the volume
limiter for this recipient.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ...config import ServiceConfig
from ...models import ScheduledSend, SendingAccount
from .timezone_detector import TimezoneDetector
from .send_time_optimizer import SendTimeOptimizer
from .volume_limiter import VolumeLimiter
from .warmup_manager import WarmupManager

logger = logging.getLogger(__name__)


class SchedulingLayer:
    def __init__(self, config: ServiceConfig, accounts: list[SendingAccount]) -> None:
        self._config = config
        self._tz = TimezoneDetector(config.default_timezone)
        self._optimizer = SendTimeOptimizer(config)
        self._limiter = VolumeLimiter(config)
        self._warmup = WarmupManager(config)

        # Apply warm-up caps up front so daily_limit reflects each account's age.
        self._accounts = [self._warmup.apply(a) for a in accounts]

    @property
    def accounts(self) -> list[SendingAccount]:
        return self._accounts

    def schedule(
        self,
        email: dict,
        step: str,
        earliest_day_offset: int = 0,
        now: Optional[datetime] = None,
    ) -> Optional[ScheduledSend]:
        """
        Produce a ScheduledSend for one email, or None if no account has capacity.

        `email` is an Agent 3 row (dict). `step` is the sequence step. For
        follow-ups, pass earliest_day_offset (e.g. 3) to push the slot out.
        """
        now = now or datetime.now(timezone.utc)
        recipient = email.get("recipient") or email.get("sender_email") or ""
        # Agent 3 rows don't carry the recipient's address directly; the lead's
        # email is the recipient. Callers pass it via the "recipient" key.
        recipient = email.get("recipient", recipient)

        account = self._pick_account(recipient, now)
        if account is None:
            logger.info(
                "SchedulingLayer: no account capacity for %s (step=%s)", recipient, step
            )
            return None

        tz_name = email.get("timezone") or self._tz.detect(email)
        send_at = self._optimizer.next_send_time(
            tz_name, not_before=now, earliest_day_offset=earliest_day_offset
        )

        # Reserve capacity against the chosen account.
        self._limiter.record(account, recipient, now)

        return ScheduledSend(
            email_id=email.get("id", ""),
            lead_id=email.get("lead_id", ""),
            step=step,
            recipient=recipient,
            account_email=account.email,
            timezone=tz_name,
            scheduled_at=send_at,
        )

    def account_for(self, email_addr: str) -> Optional[SendingAccount]:
        for a in self._accounts:
            if a.email == email_addr:
                return a
        return None

    # ── Internal ───────────────────────────────────────────────────────────────

    def _pick_account(self, recipient: str, now: datetime) -> Optional[SendingAccount]:
        # Health-weighted round-robin: best health first, then least loaded.
        ordered = sorted(
            (a for a in self._accounts if a.is_sendable),
            key=lambda a: (-a.health_score, a.sent_today),
        )
        for account in ordered:
            allowed, reason = self._limiter.can_send(account, recipient, now)
            if allowed:
                return account
            logger.debug("SchedulingLayer: skip %s — %s", account.email, reason)
        return None
