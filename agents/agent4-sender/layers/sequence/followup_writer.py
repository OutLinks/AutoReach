"""
Follow-up writer (Sequence Layer content generation).

Produces the body for a day-3 / day-7 / day-14 follow-up. Prefers the LLM
(on-thread, non-repetitive copy via prompt.build_followup_prompt) and falls back
to the deterministic templates in steps.py whenever the LLM is disabled, errors,
or returns unparseable output — so a follow-up always gets written.
"""

from __future__ import annotations

import json
import logging

from core.model_selection import get_model
from core.model_selection.types import Message

from ...config import ServiceConfig
from ...models import FollowUpDraft
from ...prompt import build_followup_prompt
from . import steps

logger = logging.getLogger(__name__)


class FollowUpWriter:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

    async def write(
        self,
        step: str,
        *,
        first_name: str,
        company: str,
        original_subject: str,
        original_body: str,
        sender_name: str,
        value_proposition: str = "",
    ) -> FollowUpDraft:
        if self._config.use_llm_followups:
            draft = await self._write_llm(
                step,
                first_name=first_name,
                company=company,
                original_subject=original_subject,
                original_body=original_body,
                sender_name=sender_name,
                value_proposition=value_proposition,
            )
            if draft is not None:
                return draft

        subject, body = steps.template_followup(
            step,
            first_name=first_name,
            company=company,
            original_subject=original_subject,
            sender_name=sender_name,
            value_proposition=value_proposition,
        )
        return FollowUpDraft(step=step, subject=subject, body=body, generated_by="template")

    async def _write_llm(self, step: str, **kw) -> FollowUpDraft | None:
        system, user = build_followup_prompt(
            step,
            lead_first_name=kw["first_name"],
            lead_company=kw["company"],
            original_subject=kw["original_subject"],
            original_body=kw["original_body"],
            sender_name=kw["sender_name"],
            value_proposition=kw.get("value_proposition", ""),
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
            raw = (response.content or "").strip()
            if raw.startswith("```"):
                raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```"))

            data = json.loads(raw)
            subject = (data.get("subject") or "").strip()
            body = (data.get("body") or "").strip()
            if not body:
                return None

            # Always append the signature deterministically.
            if kw["sender_name"] and kw["sender_name"] not in body:
                body = f"{body}\n\n{kw['sender_name']}"

            return FollowUpDraft(step=step, subject=subject, body=body, generated_by="llm")
        except Exception as exc:
            logger.warning("FollowUpWriter: LLM generation failed for %s — %s", step, exc)
            return None
