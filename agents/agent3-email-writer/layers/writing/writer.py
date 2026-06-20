"""
Writing Layer orchestrator.

Runs all four writers sequentially — each component is informed by the prior
one (subject → hook → body → CTA). Assembles the final EmailDraft.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import EmailDraft, InputContext
from .subject import SubjectGenerator
from .hook import HookWriter
from .body import BodyComposer
from .cta import CTAWriter

logger = logging.getLogger(__name__)


class WritingLayer:
    def __init__(self, config: ServiceConfig) -> None:
        self._subject = SubjectGenerator(config)
        self._hook = HookWriter(config)
        self._body = BodyComposer(config)
        self._cta = CTAWriter(config)

    async def write(self, ctx: InputContext) -> EmailDraft:
        """
        Runs all four writers in sequence and assembles a complete EmailDraft.
        Each step is given the output of the previous step as context.
        """
        lead_name = ctx.lead_first_name

        # Sequential: each step informs the next
        subject = await self._subject.generate(ctx)
        hook = await self._hook.write(ctx, subject)
        body = await self._body.compose(ctx, subject, hook)
        cta = await self._cta.write(ctx, body)

        # Assemble the full body text
        full_body = _assemble(
            first_name=lead_name,
            hook=hook,
            body=body,
            cta=cta,
            signature=ctx.sender.signature or _default_signature(ctx),
        )

        word_count = len(full_body.split())

        draft = EmailDraft(
            subject=subject,
            hook=hook,
            body=body,
            cta=cta,
            full_body=full_body,
            word_count=word_count,
        )

        logger.info(
            "WritingLayer: wrote email for %s %s — %d words, subject='%s'",
            ctx.lead_first_name,
            ctx.lead_last_name,
            word_count,
            subject,
        )
        return draft


def _assemble(
    first_name: str,
    hook: str,
    body: str,
    cta: str,
    signature: str,
) -> str:
    parts = [
        f"Hi {first_name},",
        "",
        hook,
        "",
        body,
        "",
        cta,
        "",
        signature,
    ]
    return "\n".join(parts)


def _default_signature(ctx: InputContext) -> str:
    lines = [ctx.sender.full_name]
    if ctx.sender.title:
        lines.append(ctx.sender.title)
    if ctx.sender.company:
        lines.append(ctx.sender.company)
    if ctx.sender.linkedin_url:
        lines.append(ctx.sender.linkedin_url)
    return "\n".join(lines)
