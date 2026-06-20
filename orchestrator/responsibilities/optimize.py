"""
Responsibility 5 — OPTIMIZE: learn from results and improve.

A bounded feedback controller. Each cycle it reads the funnel + recent send
outcomes and nudges two knobs within safe limits:

  - bounce rate too high  → cut daily email volume (protect reputation),
  - reply rate too low     → raise the email-quality bar (send fewer, better),
  - reply rate strong + bounces low → grow volume back toward the cap.

Adjustments are small and clamped so the system self-tunes without oscillating.
Returns a list of human-readable notes describing what it changed.
"""

from __future__ import annotations

import logging

from ..config import OrchestratorConfig
from ..models import MEETING_BOOKED, REPLIED, SENT
from ..store import OrchestratorStore

logger = logging.getLogger(__name__)

_MIN_EMAILS = 10
_MAX_QUALITY = 0.90
_MIN_QUALITY = 0.60


class Optimize:
    def __init__(self, config: OrchestratorConfig, store: OrchestratorStore) -> None:
        self._config = config
        self._store = store

    def tune(self) -> list[str]:
        notes: list[str] = []

        # Cumulative counts from the audit log (robust to leads draining away).
        sent_total = self._store.count_transitions_to(SENT)
        replies = self._store.count_transitions_to(REPLIED)
        reply_rate = replies / sent_total if sent_total else 0.0
        bounce_rate = self._bounce_rate()

        # 1. Reputation guard — cut volume on high bounces.
        if bounce_rate > self._config.reputation.bounce_rate_threshold:
            before = self._config.volume.emails_per_day
            self._config.volume.emails_per_day = max(_MIN_EMAILS, int(before * 0.8))
            if self._config.volume.emails_per_day != before:
                notes.append(
                    f"bounce rate {bounce_rate:.0%} high → emails/day {before}→"
                    f"{self._config.volume.emails_per_day}"
                )

        # 2. Quality tuning from reply rate (needs a meaningful sample).
        if sent_total >= 20:
            if reply_rate < 0.10:
                before = self._config.quality.min_email_score
                self._config.quality.min_email_score = min(_MAX_QUALITY, round(before + 0.05, 2))
                if self._config.quality.min_email_score != before:
                    notes.append(
                        f"reply rate {reply_rate:.0%} low → min email score {before}→"
                        f"{self._config.quality.min_email_score}"
                    )
            elif reply_rate > 0.30 and bounce_rate <= self._config.reputation.bounce_rate_threshold:
                before = self._config.volume.emails_per_day
                grown = min(self._config.reputation.daily_send_limit, int(before * 1.1) + 1)
                self._config.volume.emails_per_day = grown
                if grown != before:
                    notes.append(
                        f"reply rate {reply_rate:.0%} strong → emails/day {before}→{grown}"
                    )

        if notes:
            logger.info("Optimize: %s", "; ".join(notes))
        return notes

    def _bounce_rate(self) -> float:
        runs = self._store.recent_runs("send", limit=10)
        processed = sum(r["processed"] for r in runs)
        failed = sum(r["failed"] for r in runs)
        return round(failed / processed, 4) if processed else 0.0
