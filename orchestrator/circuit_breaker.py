"""
Circuit breaker + retry policy.

Circuit breaker (per stage) protects the system from hammering a failing agent or
API. Classic three-state machine:

  CLOSED    — normal; count consecutive failures.
  OPEN      — too many failures; skip the stage until the cooldown elapses.
  HALF_OPEN — cooldown passed; allow ONE trial run. Success → CLOSED, fail → OPEN.

RetryPolicy decides whether a transiently-failed lead should be retried (and when)
or dead-lettered, using the backoff schedule from config.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import RetryConfig

logger = logging.getLogger(__name__)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, stage: str, failure_threshold: int, cooldown_seconds: int) -> None:
        self._stage = stage
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at: Optional[datetime] = None

    @property
    def state(self) -> str:
        return self._state

    def allow(self, now: Optional[datetime] = None) -> bool:
        """Whether a run is permitted right now."""
        now = now or datetime.now(timezone.utc)
        if self._state == OPEN:
            if self._opened_at and (now - self._opened_at).total_seconds() >= self._cooldown:
                self._state = HALF_OPEN
                logger.info("CircuitBreaker[%s]: cooldown elapsed → half-open", self._stage)
                return True
            return False
        return True  # CLOSED or HALF_OPEN both permit a run

    def record_success(self) -> None:
        if self._state in (HALF_OPEN, OPEN):
            logger.info("CircuitBreaker[%s]: recovered → closed", self._stage)
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        if self._state == HALF_OPEN:
            self._trip(now)
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._trip(now)

    def _trip(self, now: datetime) -> None:
        self._state = OPEN
        self._opened_at = now
        logger.warning(
            "CircuitBreaker[%s]: OPEN after %d failures (cooldown %ds)",
            self._stage, self._consecutive_failures, self._cooldown,
        )


class RetryPolicy:
    def __init__(self, config: RetryConfig) -> None:
        self._config = config

    def should_retry(self, attempts: int) -> bool:
        return attempts < self._config.max_attempts

    def next_retry_at(self, attempts: int, now: Optional[datetime] = None) -> datetime:
        """When the next retry is allowed, per the backoff schedule."""
        now = now or datetime.now(timezone.utc)
        idx = min(attempts, len(self._config.backoff_seconds) - 1)
        return now + timedelta(seconds=self._config.backoff_seconds[idx])
