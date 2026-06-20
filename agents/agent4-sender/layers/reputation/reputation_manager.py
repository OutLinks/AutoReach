"""
Reputation Layer orchestrator.

Bundles the four reputation components and exposes the checks the agent runs:

  - pre-send screening: is this recipient suppressed or a likely spam trap?
  - event handling: bounce / complaint feedback from providers,
  - health gate: should we pause sending right now?

Pausing flips every sending account to "paused" so no further mail goes out until
a human (or the orchestrator) intervenes.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import ReputationStatus, SendingAccount
from ...storage.send_store import SendStore
from .bounce_handler import BounceHandler
from .complaint_handler import ComplaintHandler
from .spam_trap_detector import SpamTrapDetector
from .sender_score_monitor import SenderScoreMonitor

logger = logging.getLogger(__name__)


class ReputationLayer:
    def __init__(self, config: ServiceConfig, store: SendStore) -> None:
        self._config = config
        self._store = store
        self.bounce = BounceHandler(config, store)
        self.complaint = ComplaintHandler(store)
        self.spam_trap = SpamTrapDetector(store)
        self.monitor = SenderScoreMonitor(config, store)

    # ── Pre-send gate ──────────────────────────────────────────────────────────

    def can_email(self, recipient: str) -> tuple[bool, str]:
        """Pre-send safety check for one recipient."""
        if self._store.is_suppressed(recipient):
            return False, "suppressed"
        is_trap, reason = self.spam_trap.is_trap(recipient)
        if is_trap:
            self.spam_trap.screen(recipient)  # record the suppression
            return False, f"spam trap ({reason})"
        return True, ""

    # ── Health gate ────────────────────────────────────────────────────────────

    def health(self, account_email: str = "*") -> ReputationStatus:
        return self.monitor.evaluate(account_email)

    def enforce(self, accounts: list[SendingAccount]) -> ReputationStatus:
        """Evaluate system health and pause all accounts if critical."""
        status = self.monitor.evaluate("*")
        if status.should_pause:
            for account in accounts:
                account.status = "paused"
                self._store.upsert_account(account)
            logger.warning(
                "ReputationLayer: paused %d account(s) — %s",
                len(accounts), "; ".join(status.reasons),
            )
        return status
