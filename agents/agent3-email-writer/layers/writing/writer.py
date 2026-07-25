"""
Writing Layer orchestrator.

Runs all four writers sequentially — each component is informed by the prior
one (subject → hook → body → CTA). Assembles the final EmailDraft.
"""

from __future__ import annotations

import logging
import re

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
        if _uses_closed_project_evidence(ctx):
            draft = _write_closed_project_email(ctx)
            logger.info(
                "WritingLayer: used evidence-safe job-interest path for %s",
                ctx.lead_company,
            )
            return draft

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
    greeting = f"Hi {first_name}," if first_name.strip() else "Hi there,"
    parts = [
        greeting,
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


def _uses_closed_project_evidence(ctx: InputContext) -> bool:
    instruction = ctx.campaign_instruction.lower()
    return (
        "job-interest" in instruction
        and "candidate evidence is limited to" in instruction
        and "autoreach project" in instruction
        and "project evidence" in instruction
    )


def _write_closed_project_email(ctx: InputContext) -> EmailDraft:
    """Build a tailored job-interest email without expanding project evidence."""
    role = _role_from_evidence(ctx)
    focus, project_points, limitation = _focus_and_project_points(
        ctx.source_evidence,
        ctx.campaign_instruction,
    )
    company = ctx.lead_company or "your company"

    subject = f"Interest in {role} at {company}"
    hook = (
        f"The {role} opening at {company}, with its focus on {focus}, "
        "caught my attention."
    )
    project_sentence = (
        f"My AutoReach project uses {_join_items(project_points)}."
        if project_points
        else "My candidate evidence is limited to the AutoReach project described in the campaign."
    )
    body_parts = [
        f"I'm {ctx.sender.full_name}, and I'm writing to express my interest in this {role} opportunity.",
        project_sentence,
    ]
    if limitation:
        body_parts.append(limitation)
    body_parts.append(
        "That project-level overlap is why the role interests me; I am not "
        "assuming it proves requirements or experience beyond those stated technologies."
    )
    body = " ".join(body_parts)
    cta = (
        f"Would you be open to sharing the next step in the application process "
        f"for the {role} role?"
    )
    full_body = _assemble(
        first_name=ctx.lead_first_name,
        hook=hook,
        body=body,
        cta=cta,
        signature=ctx.sender.signature or _default_signature(ctx),
    )
    return EmailDraft(
        subject=subject,
        hook=hook,
        body=body,
        cta=cta,
        full_body=full_body,
        word_count=len(full_body.split()),
    )


def _role_from_evidence(ctx: InputContext) -> str:
    if ctx.lead_title.strip():
        return re.sub(r"\s+", " ", ctx.lead_title).strip()
    evidence = ctx.source_evidence.lower()
    for role in (
        "Senior AI Engineer",
        "Applied AI Engineer",
        "Forward Deployed AI Engineer",
        "AI Engineer",
    ):
        if role.lower() in evidence:
            return role
    return "AI engineering"


def _focus_and_project_points(
    source_evidence: str,
    campaign_instruction: str,
) -> tuple[str, list[str], str]:
    evidence = source_evidence.lower()
    available = _approved_project_points(campaign_instruction)

    def select(*preferred: str) -> list[str]:
        selected = [item for item in preferred if item in available]
        return selected or available[:4]

    if "image" in evidence and "video" in evidence:
        return (
            "image and video generation pipelines",
            select(
                "asynchronous Python multi-agent orchestration",
                "provider-neutral LLM adapters",
                "Docker",
            ),
            (
                "That project is not evidence of image or video generation "
                "experience; its relevant overlap is orchestration, LLM integration, "
                "and containerized delivery."
            ),
        )
    if "internal agentic tooling" in evidence:
        return (
            "internal agentic tooling",
            select(
                "asynchronous Python multi-agent orchestration",
                "provider-neutral LLM adapters",
                "durable jobs",
                "reliability controls",
            ),
            "",
        )
    if "complex public data" in evidence:
        return (
            "LLM-driven features for complex public data",
            select(
                "provider-neutral LLM adapters",
                "FastAPI",
                "Redis",
                "durable jobs",
                "reliability controls",
            ),
            "",
        )
    if "enterprise automation" in evidence or "automates entire companies" in evidence:
        return (
            "AI applications for enterprise automation",
            select(
                "asynchronous Python multi-agent orchestration",
                "FastAPI",
                "Redis",
                "Docker",
                "email delivery integrations",
            ),
            "",
        )
    if "production llm systems" in evidence:
        return (
            "production LLM systems and agents",
            select(
                "asynchronous Python multi-agent orchestration",
                "FastAPI",
                "provider-neutral LLM adapters",
            ),
            "",
        )
    return (
        "practical AI systems",
        available[:4],
        "",
    )


def _approved_project_points(campaign_instruction: str) -> list[str]:
    instruction = campaign_instruction.lower()
    candidates = [
        ("asynchronous Python multi-agent orchestration", "asynchronous python multi-agent orchestration"),
        ("FastAPI", "fastapi"),
        ("Redis", "redis"),
        ("Firecrawl", "firecrawl"),
        ("provider-neutral LLM adapters", "provider-neutral llm adapters"),
        ("durable jobs", "durable jobs"),
        ("reliability controls", "reliability controls"),
        ("Docker", "docker"),
        ("email delivery integrations", "email delivery integrations"),
    ]
    return [label for label, marker in candidates if marker in instruction]


def _join_items(items: list[str]) -> str:
    if len(items) < 2:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])}, and {items[-1]}"
