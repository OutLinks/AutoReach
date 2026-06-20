"""
Responsibility 6 — REPORT: tells you what's happening.

Compiles the pipeline funnel and headline conversion metrics into a DailyReport.
This is the "productivity report" the factory manager hands up — what moved, what
converted, and what needs human attention.
"""

from __future__ import annotations

import logging

from ..models import DailyReport, MEETING_BOOKED, REPLIED, SENT, HealthSnapshot
from ..store import OrchestratorStore

logger = logging.getLogger(__name__)


class Report:
    def __init__(self, store: OrchestratorStore) -> None:
        self._store = store

    def compile(
        self,
        health: HealthSnapshot,
        optimizations: list[str] | None = None,
    ) -> DailyReport:
        funnel = self._store.count_by_state()

        # Headline metrics are cumulative (from the audit log), so they stay
        # correct even after leads drain into terminal states.
        sent_total = self._store.count_transitions_to(SENT)
        replies = self._store.count_transitions_to(REPLIED)
        meetings = self._store.count_transitions_to(MEETING_BOOKED)

        report = DailyReport(
            funnel=funnel,
            sent_today=sent_total,
            replies_today=replies,
            meetings_booked=meetings,
            reply_rate=round(replies / sent_total, 4) if sent_total else 0.0,
            meeting_rate=round(meetings / sent_total, 4) if sent_total else 0.0,
            dead_letter_count=health.dead_letter_count,
            alerts=list(health.alerts),
            optimizations=list(optimizations or []),
        )
        return report

    @staticmethod
    def render(report: DailyReport) -> str:
        """A compact text summary for logs / console / the human's inbox."""
        lines = [
            "── AutoReach daily report ──",
            f"Funnel: " + ", ".join(f"{k}={v}" for k, v in sorted(report.funnel.items())),
            f"Sent: {report.sent_today}  Replies: {report.replies_today} "
            f"({report.reply_rate:.0%})  Meetings: {report.meetings_booked} "
            f"({report.meeting_rate:.0%})",
            f"Dead letter: {report.dead_letter_count}",
        ]
        if report.alerts:
            lines.append("Alerts: " + "; ".join(report.alerts))
        if report.optimizations:
            lines.append("Tuning: " + "; ".join(report.optimizations))
        return "\n".join(lines)
