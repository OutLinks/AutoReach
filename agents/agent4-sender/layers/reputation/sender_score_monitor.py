"""
Sender-score monitor (Reputation Layer #4).

Reads the aggregate send/bounce/complaint/open counts out of the store and
computes a composite sender score plus a health level, applying the thresholds
from the architecture doc:

  warning:  bounce > 2%,  complaint > 0.1%, open < 15%
  critical: bounce > 5%,  complaint > 0.3%   → pause sending immediately

Returns a ReputationStatus the agent uses to decide whether to keep sending.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import ReputationStatus
from ...storage.send_store import SendStore

logger = logging.getLogger(__name__)


class SenderScoreMonitor:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store

    def evaluate(self, account_email: str = "*") -> ReputationStatus:
        m = self._store.metrics(None if account_email == "*" else account_email)
        sent = m["sent"]

        status = ReputationStatus(account_email=account_email, **m)
        if sent == 0:
            return status  # nothing sent yet → healthy defaults

        status.bounce_rate = m["bounced"] / sent
        status.complaint_rate = m["complained"] / sent
        status.open_rate = m["opened"] / sent

        reasons: list[str] = []
        level = "healthy"

        # Critical checks (pause).
        if status.bounce_rate > self._config.bounce_rate_critical:
            level = "critical"
            reasons.append(f"bounce rate {status.bounce_rate:.1%} > critical")
        if status.complaint_rate > self._config.complaint_rate_critical:
            level = "critical"
            reasons.append(f"complaint rate {status.complaint_rate:.2%} > critical")

        # Warning checks (don't escalate above critical).
        if level != "critical":
            if status.bounce_rate > self._config.bounce_rate_warning:
                level = "warning"
                reasons.append(f"bounce rate {status.bounce_rate:.1%} > warning")
            if status.complaint_rate > self._config.complaint_rate_warning:
                level = "warning"
                reasons.append(f"complaint rate {status.complaint_rate:.2%} > warning")
            if status.open_rate < self._config.open_rate_warning:
                level = "warning"
                reasons.append(f"open rate {status.open_rate:.1%} < warning")

        status.level = level
        status.reasons = reasons
        status.should_pause = level == "critical"
        status.sender_score = self._score(status)

        if status.should_pause:
            logger.warning(
                "SenderScoreMonitor: %s CRITICAL — %s", account_email, "; ".join(reasons)
            )
        return status

    def _score(self, s: ReputationStatus) -> float:
        """Composite 0–100: start at 100, penalize bounces/complaints, reward opens."""
        score = 100.0
        score -= s.bounce_rate * 600        # 5% bounce → -30
        score -= s.complaint_rate * 8000    # 0.3% complaint → -24
        if s.open_rate < self._config.open_rate_warning:
            score -= (self._config.open_rate_warning - s.open_rate) * 50
        return round(max(0.0, min(100.0, score)), 1)
