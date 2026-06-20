"""
Send-time optimizer (Scheduling Layer #2).

Given a lead's timezone and the configured policy, computes the next UTC instant
at which the email should go out:

  - target hour (default 10:00) in the lead's *local* time,
  - skipping the days the policy says to avoid (Mon/Fri/weekend by default),
  - never in the past — rolls forward to the next eligible slot.

Returns an aware UTC datetime so the scheduler can sort a mixed-timezone batch
on a single axis.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...config import ServiceConfig

logger = logging.getLogger(__name__)


class SendTimeOptimizer:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    def next_send_time(
        self,
        tz_name: str,
        not_before: datetime | None = None,
        earliest_day_offset: int = 0,
    ) -> datetime:
        """
        Compute the next eligible send instant (UTC, tz-aware).

        Args:
            tz_name: IANA timezone of the lead.
            not_before: don't schedule before this UTC instant (defaults to now).
            earliest_day_offset: minimum days out (used by follow-ups, e.g. +3).
        """
        tz = self._zone(tz_name)
        now_utc = (not_before or datetime.now(timezone.utc)).astimezone(timezone.utc)

        # Start searching from the local date corresponding to now + offset.
        local_now = now_utc.astimezone(tz)
        candidate_date = (local_now + timedelta(days=earliest_day_offset)).date()
        target = time(hour=self._config.target_send_hour)

        for _ in range(14):  # look ahead up to two weeks for an eligible slot
            local_dt = datetime.combine(candidate_date, target, tzinfo=tz)
            utc_dt = local_dt.astimezone(timezone.utc)

            eligible_day = candidate_date.weekday() not in self._config.avoid_weekdays
            in_future = utc_dt > now_utc

            if eligible_day and in_future:
                return utc_dt

            candidate_date += timedelta(days=1)

        # Fallback: target hour tomorrow, policy be damned (should never hit).
        local_dt = datetime.combine(
            (local_now + timedelta(days=1)).date(), target, tzinfo=tz
        )
        return local_dt.astimezone(timezone.utc)

    def is_within_business_hours(self, tz_name: str, instant: datetime) -> bool:
        local = instant.astimezone(self._zone(tz_name))
        return self._config.business_hour_start <= local.hour < self._config.business_hour_end

    def _zone(self, tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            logger.warning("SendTimeOptimizer: unknown tz %r, using UTC", tz_name)
            return ZoneInfo("UTC")
