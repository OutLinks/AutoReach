"""
Quality Layer orchestrator.

Runs all four quality checks and aggregates into a QualityReport.
Spam, length, and tone run in parallel (all rule-based and instant).
Personalization runs in parallel alongside them (LLM-based but independent).

If the report fails and revision is configured, the caller can request
a revision and pass the report back through the checker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import EmailDraft, InputContext, QualityReport
from ...prompt import build_revision_prompt
from . import spam, length, tone, personalization

logger = logging.getLogger(__name__)


class QualityLayer:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def check(self, draft: EmailDraft, ctx: InputContext) -> QualityReport:
        """Run all quality checks concurrently and return an aggregate report."""
        tasks = []

        # Rule-based checks (sync wrapped in coroutines for gather)
        if self._config.run_spam_check:
            tasks.append(self._run_sync(spam.check, draft.full_body))
        if self._config.run_length_check:
            tasks.append(self._run_sync(length.check, draft.full_body, self._config))
        if self._config.run_tone_check:
            tasks.append(self._run_sync(tone.check, draft.full_body, ctx.brand_voice))

        # LLM-based check (already async)
        if self._config.run_personalization_check:
            tasks.append(personalization.check(draft.full_body, ctx, self._config))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        report = QualityReport()
        for result in results:
            if isinstance(result, Exception):
                logger.warning("QualityLayer: check raised — %s", result)
            else:
                report.checks.append(result)

        report.compute_overall()
        self._log_report(ctx, report)
        return report

    async def revise(
        self,
        draft: EmailDraft,
        ctx: InputContext,
        report: QualityReport,
    ) -> Optional[EmailDraft]:
        """
        Ask the LLM to revise a failing email based on the quality report.
        Returns a new EmailDraft or None if revision fails.
        """
        all_issues = [i for check in report.checks for i in check.issues]
        all_suggestions = [s for check in report.checks for s in check.suggestions]

        if not all_issues:
            return None

        system, user = build_revision_prompt(
            full_email=draft.full_body,
            subject=draft.subject,
            issues=all_issues,
            suggestions=all_suggestions,
            ctx=ctx,
        )

        try:
            adapter = get_model(self._config.model)
            response = await adapter.complete(
                self._config.model,
                [
                    Message(role="system", content=system),
                    Message(role="user", content=user),
                ],
            )
            raw = (response.content or "{}").strip()
            if raw.startswith("```"):
                raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```"))

            data = json.loads(raw)
            revised_subject = data.get("subject", draft.subject).strip()
            revised_body = data.get("body", "").strip()

            if not revised_body:
                return None

            revised_draft = EmailDraft(
                subject=revised_subject,
                hook=draft.hook,
                body=draft.body,
                cta=draft.cta,
                full_body=revised_body,
                word_count=len(revised_body.split()),
            )
            logger.info(
                "QualityLayer: revised email for %s %s",
                ctx.lead_first_name,
                ctx.lead_last_name,
            )
            return revised_draft

        except Exception as exc:
            logger.warning("QualityLayer: revision failed — %s", exc)
            return None

    @staticmethod
    async def _run_sync(fn, *args):
        """Wrap a synchronous checker so it can run in asyncio.gather."""
        return fn(*args)

    def _log_report(self, ctx: InputContext, report: QualityReport) -> None:
        status = "PASS" if report.overall_passed else "FAIL"
        checks_summary = ", ".join(
            f"{c.checker}={'✓' if c.passed else '✗'}({c.score:.2f})"
            for c in report.checks
        )
        logger.info(
            "QualityLayer: %s %s — overall=%.2f [%s]",
            ctx.lead_first_name,
            ctx.lead_last_name,
            report.overall_score,
            checks_summary,
        )
