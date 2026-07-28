"""Execution bridge between interactive API workflows and the existing agents."""

from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.runtime_paths import agent_output_dir
from orchestrator import models as pipeline_models
from orchestrator.adapters.live import _load_agent
from orchestrator.state_machine import STAGE_BY_NAME

from .workflows import WorkflowStore


class WorkflowRunner:
    def __init__(self, workflows: WorkflowStore, orchestrator: Any) -> None:
        self.workflows = workflows
        self.orchestrator = orchestrator
        self._sender: Any | None = None

    async def execute(self, kind: str, payload: dict[str, Any], job_id: str) -> Any:
        handlers = {
            "research.create": self._research_create,
            "research.iterate": self._research_iterate,
            "email.generate": self._email_generate,
            "email.regenerate": self._email_regenerate,
            "email.send": self._email_send,
            "mailbox.reply": self._mailbox_reply,
            "lead_search.run": self._lead_search,
            "orchestrator.message": self._orchestrator_message,
        }
        try:
            handler = handlers[kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported interactive job kind: {kind}") from exc
        return await handler(payload, job_id)

    def close(self) -> None:
        if self._sender is not None:
            self._sender.close()
            self._sender = None

    # Research

    async def _research_create(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        lead_id = payload.get("lead_id") or ""
        lead = self.orchestrator.store.get_lead(lead_id) if lead_id else None
        company = (payload.get("company") or (lead.company if lead else "")).strip()
        prompt = (payload.get("prompt") or "").strip()
        if not company:
            raise ValueError("A company or a lead with a company is required")

        if self.orchestrator.config.simulate:
            document = self._simulated_research(lead_id, company, prompt)
        else:
            document = await self._live_research(lead_id, company, prompt)
        created = self.workflows.create_research(
            lead_id=lead_id,
            company=company,
            prompt=prompt,
            summary=document["summary"],
            sections=document["sections"],
            sources=document["sources"],
        )
        return {"research_id": created["id"], "document": created}

    async def _research_iterate(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        document = self.workflows.get_research(payload["research_id"])
        instructions = payload["instructions"].strip()
        if self.orchestrator.config.simulate:
            sections = list(document["sections"])
            sections.append(
                {
                    "heading": "Iteration",
                    "body": f"Refinement requested: {instructions}",
                }
            )
            values = {
                "summary": document["summary"],
                "sections": sections,
                "sources": document["sources"],
            }
        else:
            values = await self._refine_research(document, instructions)
        updated = self.workflows.update_research(document["id"], values)
        return {"research_id": updated["id"], "document": updated}

    def _simulated_research(
        self, lead_id: str, company: str, prompt: str
    ) -> dict[str, Any]:
        lead = self.orchestrator.store.get_lead(lead_id) if lead_id else None
        industry = lead.industry if lead else ""
        focus = prompt or "company fit, current priorities, and credible outreach angles"
        summary = (
            f"{company} is represented by a simulated research profile"
            f"{f' in the {industry} sector' if industry else ''}. "
            f"The requested focus is {focus}."
        )
        return {
            "summary": summary,
            "sections": [
                {
                    "heading": "Company overview",
                    "body": (
                        f"Review {company}'s public positioning, customers, and growth stage "
                        "before using this profile for live outreach."
                    ),
                },
                {
                    "heading": "Potential priorities",
                    "body": (
                        "Validate hiring, product, and operational signals against primary "
                        "sources before referencing them in an email."
                    ),
                },
                {
                    "heading": "Outreach angle",
                    "body": (
                        "Lead with a verified company-specific observation and connect it "
                        "to a concrete outcome without inventing facts."
                    ),
                },
            ],
            "sources": [],
        }

    async def _live_research(
        self, lead_id: str, company: str, prompt: str
    ) -> dict[str, Any]:
        module = _load_agent("agent2-research-analyst", "agent2_research_analyst")
        config = module.ServiceConfig()
        config.campaign_instruction = prompt
        lead_payload = self._lead_payload(lead_id, company)
        if not (
            lead_payload.get("website") or lead_payload.get("company_website")
        ):
            supplied_url = re.search(r"https?://[^\s<>()\[\]{}\"']+", prompt)
            if supplied_url:
                lead_payload["company_website"] = supplied_url.group(0).rstrip(
                    ".,;:!?)"
                )

        # Existing lead records can use the full Agent 2 batch contract. A
        # company-only request uses the same collection and analysis layers on a
        # synthetic company record because Agent 2's file reader requires an id.
        if lead_id and self._agent1_lead(lead_id):
            agent = module.ResearchAgent(config)
            await agent.run(job_id=job_id, lead_ids=[lead_id])
            profile = self._latest_json_record(
                agent_output_dir("agent2-research-analyst").glob(
                    f"research_{job_id[:8]}_*.jsonl"
                ),
                key="lead_id",
                value=lead_id,
            )
        else:
            from agent2_research_analyst.layers.analysis.analyzer import AnalysisLayer
            from agent2_research_analyst.layers.collection.collector import DataCollector
            from agent2_research_analyst.models import ResearchProfile

            raw = await DataCollector(config).collect(lead_payload)
            research_profile = ResearchProfile(
                lead_id=lead_id or f"company:{uuid4()}",
                raw_data=raw,
                research_completeness=raw.completeness,
                sources_used=raw.sources_succeeded,
                status="in_progress",
            )
            await AnalysisLayer(config).analyze(lead_payload, raw, research_profile)
            research_profile.mark_complete()
            profile = research_profile.model_dump(mode="json")
        if not profile:
            raise ValueError("Research agent did not produce a document")
        return self._research_document_from_profile(company, profile)

    async def _refine_research(
        self, document: dict[str, Any], instructions: str
    ) -> dict[str, Any]:
        from core.model_selection import Message, get_model, model_config_from_env

        config = model_config_from_env(max_tokens=4096, temperature=0.2)
        response = await get_model(config).complete(
            config,
            [
                Message(
                    role="system",
                    content=(
                        "Revise the supplied research document. Preserve verified facts and "
                        "source URLs. Return JSON with summary, sections (heading/body), and "
                        "sources (title/url) only."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {"document": document, "instructions": instructions},
                        default=str,
                    ),
                ),
            ],
        )
        values = self._extract_json_object(response.content or "")
        return {
            "summary": str(values.get("summary") or document["summary"]),
            "sections": values.get("sections") or document["sections"],
            "sources": values.get("sources") or document["sources"],
        }

    # Email writing

    async def _email_generate(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        lead = self.orchestrator.store.get_lead(payload["lead_id"])
        if lead is None:
            raise ValueError("Lead not found")
        campaign = self.orchestrator.store.get_campaign(payload["campaign_id"])
        if campaign is None:
            raise ValueError("Campaign not found")
        tone = (payload.get("tone") or "").strip()
        instructions = (payload.get("instructions") or "").strip()
        content = await self._write_email(
            lead=lead,
            campaign=campaign,
            tone=tone,
            instructions=instructions,
            job_id=job_id,
        )
        draft = self.workflows.create_draft(
            lead_id=lead.id,
            campaign_id=campaign.id,
            subject=content["subject"],
            body=content["body"],
            tone=tone,
            instructions=instructions,
            source_email_id=content.get("source_email_id", ""),
        )
        return {"draft_id": draft["id"], "draft": draft}

    async def _email_regenerate(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        original = self.workflows.get_draft_internal(payload["draft_id"])
        lead = self.orchestrator.store.get_lead(original["lead_id"])
        campaign = self.orchestrator.store.get_campaign(original["campaign_id"])
        if lead is None or campaign is None:
            raise ValueError("Draft lead or campaign no longer exists")
        extra = (payload.get("instructions") or "").strip()
        instructions = "\n".join(
            part
            for part in (
                original["instructions"],
                extra,
                "Produce a meaningfully different variant from the previous draft.",
                f"Previous subject: {original['subject']}",
                f"Previous body: {original['body']}",
            )
            if part
        )
        content = await self._write_email(
            lead=lead,
            campaign=campaign,
            tone=original["tone"],
            instructions=instructions,
            job_id=job_id,
        )
        variant = self.workflows.create_draft(
            lead_id=lead.id,
            campaign_id=campaign.id,
            subject=content["subject"],
            body=content["body"],
            tone=original["tone"],
            instructions=instructions,
            source_email_id=content.get("source_email_id", ""),
        )
        return {
            "draft_id": variant["id"],
            "variant_of": original["id"],
            "draft": variant,
        }

    async def _write_email(
        self,
        *,
        lead: Any,
        campaign: Any,
        tone: str,
        instructions: str,
        job_id: str,
    ) -> dict[str, str]:
        company = lead.company or "your team"
        research_documents = self.workflows.list_research(lead.id)
        research_document = research_documents[0] if research_documents else None
        if self.orchestrator.config.simulate:
            subject = f"An idea for {company}"
            research_reference = (
                f"Based on the current research: {research_document['summary']}\n\n"
                if research_document
                else ""
            )
            body = (
                f"Hi there,\n\nI noticed the work underway at {company}. "
                f"{campaign.messaging.value_proposition or 'I wanted to share a relevant idea.'}\n\n"
                f"{research_reference}"
                f"{instructions + chr(10) + chr(10) if instructions else ''}"
                "Would a short conversation next week be useful?\n\nBest"
            )
            return {"subject": subject, "body": body}

        module = _load_agent("agent3-email-writer", "agent3_email_writer")
        config = module.ServiceConfig()
        config.db_path = self.orchestrator.config.db_path
        config.campaign_instruction = "\n".join(
            value
            for value in (
                campaign.instruction_for("email_writer"),
                f"Requested tone: {tone}" if tone else "",
                instructions,
            )
            if value
        )
        if research_document:
            return await self._write_from_editable_research(
                config,
                lead,
                research_document,
                tone,
            )
        agent = module.EmailWriterAgent(config)
        await agent.run(job_id=job_id, lead_ids=[lead.id], min_quality_score=0)
        with closing(self._sqlite()) as connection:
            row = connection.execute(
                """
                SELECT id, subject, body FROM emails
                WHERE job_id = ? AND lead_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id, lead.id),
            ).fetchone()
            # API-generated copy always requires an explicit approval. Prevent
            # the batch sender from seeing Agent 3's quality-gate approval as a
            # user approval.
            connection.execute(
                """
                UPDATE emails SET status = 'draft'
                WHERE job_id = ? AND lead_id = ?
                """,
                (job_id, lead.id),
            )
            connection.commit()
        if row is None:
            raise ValueError("Email writer did not produce a draft")
        return {
            "source_email_id": row["id"],
            "subject": row["subject"],
            "body": row["body"],
        }

    async def _write_from_editable_research(
        self,
        config: Any,
        lead: Any,
        document: dict[str, Any],
        tone: str,
    ) -> dict[str, str]:
        """Run Agent 3's writing/quality layers against the latest user-edited research."""
        from agent3_email_writer.layers.input.assembler import InputAssembler
        from agent3_email_writer.layers.input.brand_voice import BrandVoiceLoader
        from agent3_email_writer.layers.input.sender_profile import SenderProfileLoader
        from agent3_email_writer.layers.quality.checker import QualityLayer
        from agent3_email_writer.layers.writing.writer import WritingLayer

        lead_payload = self._agent1_lead(lead.id) or {
            "id": lead.id,
            "email": lead.email,
            "company_name": lead.company,
            "industry": lead.industry,
        }
        evidence = "\n\n".join(
            f"{section['heading']}\n{section['body']}"
            for section in document["sections"]
        )
        profile = {
            "id": document["id"],
            "lead_id": lead.id,
            "company_profile": {"summary": document["summary"]},
            "raw_data": {"website_pages": {"editable_research": evidence}},
            "quality_score": {"score": 10},
        }
        context = InputAssembler(
            BrandVoiceLoader().load(),
            SenderProfileLoader().load(),
        ).assemble(profile, lead_payload, config.campaign_instruction)
        if tone:
            context.recommended_tone = tone
        writing = WritingLayer(config)
        quality = QualityLayer(config)
        draft = await writing.write(context)
        report = await quality.check(draft, context)
        if not report.overall_passed and config.max_revision_attempts > 0:
            revised = await quality.revise(draft, context, report)
            if revised is not None:
                draft = revised
        return {"subject": draft.subject, "body": draft.full_body}

    async def _email_send(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        draft = self.workflows.get_draft_internal(payload["draft_id"])
        if draft["status"] != "approved":
            raise ValueError("Draft must be approved before sending")
        lead = self.orchestrator.store.get_lead(draft["lead_id"])
        if lead is None:
            raise ValueError("Lead not found")
        if not lead.email and not self.orchestrator.config.simulate:
            raise ValueError("Lead does not have a recipient email")
        recipient = lead.email or f"simulated@{lead.id}.invalid"
        sent = await self._sender_agent().send_message(
            email_id=draft["source_email_id"] or draft["id"],
            lead_id=lead.id,
            recipient=recipient,
            subject=draft["subject"],
            body=draft["body"],
            job_id=job_id,
        )
        if sent is None:
            raise ValueError("Sender did not accept the email")
        self.workflows.set_draft_status(draft["id"], "sent")
        if draft["source_email_id"]:
            self._set_legacy_email_status(draft["source_email_id"], "sent")
        self._record_sent_message(sent)
        return {
            "draft_id": draft["id"],
            "sent_email_id": sent.id,
            "simulated": bool(self.orchestrator.config.simulate),
        }

    # Mailbox

    async def _mailbox_reply(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        thread = self.workflows.get_mailbox_thread(payload["thread_id"])
        lead = self.orchestrator.store.get_lead(thread["lead_id"])
        if lead is None:
            raise ValueError("Thread lead not found")
        prior = thread["messages"][-1] if thread["messages"] else {}
        recipient = prior.get("from") if prior.get("direction") == "in" else lead.email
        if not recipient:
            raise ValueError("Thread does not have a reply recipient")
        subject = payload.get("subject") or thread["subject"]
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        sent = await self._sender_agent().send_message(
            email_id=f"mailbox:{thread['id']}",
            lead_id=lead.id,
            recipient=recipient,
            subject=subject,
            body=payload["body"],
            job_id=job_id,
            step="manual_reply",
            in_reply_to=self._latest_provider_message_id(lead.id),
            references=self._latest_provider_message_id(lead.id),
        )
        if sent is None:
            raise ValueError("Sender did not accept the reply")
        self._record_sent_message(sent)
        self.workflows.mark_thread_read(thread["id"])
        return {
            "thread_id": thread["id"],
            "sent_email_id": sent.id,
            "simulated": bool(self.orchestrator.config.simulate),
        }

    def record_sender_event(
        self,
        payload: dict[str, Any],
        result: Any,
        job_id: str,
    ) -> None:
        with closing(self._sqlite()) as connection:
            row = connection.execute(
                "SELECT * FROM sent_emails WHERE id = ?",
                (payload["sent_email_id"],),
            ).fetchone()
        if row is None:
            return
        sent = dict(row)
        if payload["event"] == "reply":
            self.workflows.add_mailbox_message(
                message_id=f"inbound:{job_id}",
                thread_id=sent["lead_id"],
                lead_id=sent["lead_id"],
                direction="in",
                from_addr=sent["recipient"],
                to_addr=sent["account_email"],
                subject=self._reply_subject(sent["subject"]),
                body=payload.get("detail") or "",
            )
        elif payload["event"] == "bounce":
            self.workflows.add_mailbox_message(
                message_id=f"sent:{sent['id']}",
                thread_id=sent["lead_id"],
                lead_id=sent["lead_id"],
                direction="out",
                from_addr=sent["account_email"],
                to_addr=sent["recipient"],
                subject=sent["subject"],
                body=sent["body"],
                sent_at=sent["sent_at"] or sent["created_at"],
                bounced=True,
            )

    def sync_mailbox_history(self) -> None:
        """Materialize Agent 4 sends/events into the unified mailbox tables."""
        try:
            with closing(self._sqlite()) as connection:
                sends = connection.execute(
                    "SELECT * FROM sent_emails ORDER BY created_at"
                ).fetchall()
                replies = connection.execute(
                    """
                    SELECT e.*, s.recipient, s.account_email, s.subject
                    FROM tracking_events e
                    JOIN sent_emails s ON s.id = e.sent_email_id
                    WHERE e.event_type = 'reply'
                    ORDER BY e.occurred_at
                    """
                ).fetchall()
        except Exception:
            return
        for row in sends:
            sent = dict(row)
            self.workflows.add_mailbox_message(
                message_id=f"sent:{sent['id']}",
                thread_id=sent["lead_id"],
                lead_id=sent["lead_id"],
                direction="out",
                from_addr=sent["account_email"],
                to_addr=sent["recipient"],
                subject=sent["subject"],
                body=sent["body"],
                sent_at=sent["sent_at"] or sent["created_at"],
                bounced=bool(sent["bounced"]),
            )
        for row in replies:
            reply = dict(row)
            self.workflows.add_mailbox_message(
                message_id=f"event:{reply['id']}",
                thread_id=reply["lead_id"],
                lead_id=reply["lead_id"],
                direction="in",
                from_addr=reply["recipient"],
                to_addr=reply["account_email"],
                subject=self._reply_subject(reply["subject"]),
                body=reply["detail"] or "",
                sent_at=reply["occurred_at"],
            )

    # Lead finding

    async def _lead_search(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        search_id = payload["search_id"]
        search = self.workflows.get_search(search_id)
        try:
            if self.orchestrator.config.simulate:
                results = self._simulated_search_results(
                    search_id,
                    search["query"],
                    search["filters"],
                    payload["limit"],
                )
            else:
                results = await self._live_search_results(
                    job_id,
                    search["query"],
                    search["filters"],
                    payload["limit"],
                )
            self.workflows.finish_search(search_id, results)
        except Exception as exc:
            self.workflows.fail_search(search_id, str(exc))
            raise
        return {"search_id": search_id, "found_count": len(results)}

    def _simulated_search_results(
        self,
        search_id: str,
        query: str,
        filters: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        industry = filters.get("industry") or "Technology"
        location = filters.get("location") or ""
        size = filters.get("size") or ""
        return [
            {
                "id": f"search-{search_id[:8]}-{index + 1:03d}",
                "first_name": f"Lead{index + 1}",
                "last_name": "Preview",
                "full_name": f"Lead {index + 1} Preview",
                "email": f"preview{index + 1}@example.invalid",
                "company_name": f"{industry} Company {index + 1}",
                "company_description": (
                    f"Simulated public-web description for "
                    f"{industry} Company {index + 1}."
                ),
                "industry": industry,
                "company_size": size,
                "city": location,
                "title": "AI Engineering Leader",
                "lead_score": 80 - index,
                "lead_grade": "A",
                "stage": "complete",
                "sources": ["simulation"],
                "search_query": query,
            }
            for index in range(limit)
        ]

    async def _live_search_results(
        self,
        job_id: str,
        query: str,
        filters: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        module = _load_agent("agent1-lead-finder", "agent1_lead_finder")
        config = module.ServiceConfig()
        config.max_leads_per_run = min(config.max_leads_per_run, limit)
        prompt_parts = [query]
        for label in ("industry", "location", "size"):
            if filters.get(label):
                prompt_parts.append(f"{label}: {filters[label]}")
        prompt_parts.append(f"Return at most {limit} leads.")
        await module.LeadFinderAgent(config).run("\n".join(prompt_parts), job_id=job_id)
        records: list[dict[str, Any]] = []
        for path in sorted(
            agent_output_dir("agent1-lead-finder").glob(
                f"leads_{job_id[:8]}_*.jsonl"
            )
        ):
            records.extend(self._read_jsonl(path))
        return records[:limit]

    # Natural-language orchestrator

    async def _orchestrator_message(
        self, payload: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        conversation_id = payload["conversation_id"]
        message = payload["message"].lower()
        action, agent = self._operator_action(message)
        context = payload.get("context") or {}
        if action == "pipeline.stage:research" and context.get("lead_id"):
            action = "research.create"
        elif (
            action == "pipeline.stage:write"
            and context.get("lead_id")
            and context.get("campaign_id")
        ):
            action = "email.generate"
        timeline_id = self.workflows.add_timeline_step(
            conversation_id,
            step="execute",
            agent=agent,
            action=action,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            if action == "pipeline.find":
                result = await self.orchestrator.run_find()
            elif action == "research.create":
                result = await self._research_create(
                    {
                        "lead_id": context["lead_id"],
                        "prompt": payload["message"],
                    },
                    job_id,
                )
            elif action == "email.generate":
                result = await self._email_generate(
                    {
                        "lead_id": context["lead_id"],
                        "campaign_id": context["campaign_id"],
                        "instructions": payload["message"],
                    },
                    job_id,
                )
            elif action == "pipeline.cycle":
                result = await self.orchestrator.run_cycle()
            elif action.startswith("pipeline.stage:"):
                stage_name = action.split(":", 1)[1]
                result = await self.orchestrator.run_stage(STAGE_BY_NAME[stage_name])
            elif action == "pipeline.report":
                result = self.orchestrator.report()
            else:
                result = self.orchestrator.health()
            self.workflows.update_timeline_step(timeline_id, "succeeded")
        except Exception:
            self.workflows.update_timeline_step(timeline_id, "failed")
            raise

        serialized = self._jsonable(result)
        self.workflows.add_operator_message(
            conversation_id,
            "tool",
            json.dumps(serialized, default=str),
            tool_calls=[{"name": action, "job_id": job_id}],
        )
        response = f"Completed {action} successfully."
        self.workflows.add_operator_message(conversation_id, "agent", response)
        return {
            "conversation_id": conversation_id,
            "action": action,
            "result": serialized,
        }

    # Helpers

    def _sender_agent(self) -> Any:
        if self._sender is None:
            module = _load_agent("agent4-sender", "agent4_sender")
            config = module.ServiceConfig.from_env()
            config.db_path = self.orchestrator.config.db_path
            config.emails_db_path = self.orchestrator.config.db_path
            self._sender = module.SenderAgent(config)
        return self._sender

    def _record_sent_message(self, sent: Any) -> None:
        self.workflows.add_mailbox_message(
            message_id=f"sent:{sent.id}",
            thread_id=sent.lead_id,
            lead_id=sent.lead_id,
            direction="out",
            from_addr=sent.account_email,
            to_addr=sent.recipient,
            subject=sent.subject,
            body=sent.body,
            sent_at=(sent.sent_at or sent.created_at).isoformat(),
        )

    def _latest_provider_message_id(self, lead_id: str) -> str:
        with closing(self._sqlite()) as connection:
            row = connection.execute(
                """
                SELECT message_id FROM sent_emails
                WHERE lead_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (lead_id,),
            ).fetchone()
        return row["message_id"] if row else ""

    def _set_legacy_email_status(self, email_id: str, status: str) -> None:
        with closing(self._sqlite()) as connection:
            connection.execute(
                "UPDATE emails SET status = ? WHERE id = ?",
                (status, email_id),
            )
            connection.commit()

    def _sqlite(self):
        import sqlite3

        connection = sqlite3.connect(self.orchestrator.config.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _lead_payload(self, lead_id: str, company: str) -> dict[str, Any]:
        raw = self._agent1_lead(lead_id) if lead_id else None
        if raw:
            return raw
        lead = self.orchestrator.store.get_lead(lead_id) if lead_id else None
        return {
            "id": lead_id or f"company:{uuid4()}",
            "company_name": company,
            "email": lead.email if lead else "",
            "industry": lead.industry if lead else "",
            "full_name": "",
        }

    def _agent1_lead(self, lead_id: str) -> dict[str, Any] | None:
        if not lead_id:
            return None
        for path in sorted(agent_output_dir("agent1-lead-finder").glob("leads_*.jsonl")):
            for lead in self._read_jsonl(path):
                if lead.get("id") == lead_id:
                    return lead
        return None

    @staticmethod
    def _research_document_from_profile(
        company: str, profile: dict[str, Any]
    ) -> dict[str, Any]:
        company_profile = profile.get("company_profile") or {}
        personal = profile.get("personal_profile") or {}
        angle = profile.get("email_angle") or {}
        pain_points = profile.get("pain_points") or []
        raw = profile.get("raw_data") or {}
        summary = company_profile.get("summary") or raw.get("public_company_profile") or (
            f"Research profile for {company}."
        )
        sections: list[dict[str, str]] = []
        if company_profile:
            sections.append(
                {
                    "heading": "Company",
                    "body": json.dumps(company_profile, ensure_ascii=False, indent=2),
                }
            )
        if pain_points:
            sections.append(
                {
                    "heading": "Pain points",
                    "body": json.dumps(pain_points, ensure_ascii=False, indent=2),
                }
            )
        if personal:
            sections.append(
                {
                    "heading": "Decision maker",
                    "body": json.dumps(personal, ensure_ascii=False, indent=2),
                }
            )
        if angle:
            sections.append(
                {
                    "heading": "Email angle",
                    "body": json.dumps(angle, ensure_ascii=False, indent=2),
                }
            )
        sources: list[dict[str, str]] = []
        for result in raw.get("web_search_results") or []:
            if result.get("url"):
                sources.append(
                    {"title": result.get("title") or result["url"], "url": result["url"]}
                )
        for article in raw.get("news_articles") or []:
            if article.get("url"):
                sources.append(
                    {
                        "title": article.get("title") or article["url"],
                        "url": article["url"],
                    }
                )
        for platform, url in (raw.get("social_profiles") or {}).items():
            if url:
                sources.append({"title": platform, "url": url})
        return {"summary": summary, "sections": sections, "sources": sources}

    @staticmethod
    def _latest_json_record(
        paths: Any, *, key: str, value: str
    ) -> dict[str, Any] | None:
        matched: dict[str, Any] | None = None
        for path in sorted(paths):
            for record in WorkflowRunner._read_jsonl(path):
                if record.get(key) == value:
                    matched = record
        return matched

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return records

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        candidate = fenced.group(1) if fenced else text
        if not fenced:
            bare = re.search(r"\{.*\}", text, re.DOTALL)
            candidate = bare.group(0) if bare else candidate
        value = json.loads(candidate)
        if not isinstance(value, dict):
            raise ValueError("Model response was not a JSON object")
        return value

    @staticmethod
    def _reply_subject(subject: str) -> str:
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"

    @staticmethod
    def _operator_action(message: str) -> tuple[str, str]:
        if any(word in message for word in ("find", "discover", "search lead")):
            return "pipeline.find", "agent1-lead-finder"
        if "research" in message:
            return "pipeline.stage:research", "agent2-research-analyst"
        if any(word in message for word in ("write", "draft", "email cop")):
            return "pipeline.stage:write", "agent3-email-writer"
        if any(word in message for word in ("follow up", "follow-up", "followup")):
            return "pipeline.stage:followup", "agent4-sender"
        if any(word in message for word in ("send", "outreach")):
            return "pipeline.stage:send", "agent4-sender"
        if "repl" in message:
            return "pipeline.stage:reply", "agent5-reply-handler"
        if any(word in message for word in ("cycle", "run pipeline", "execute")):
            return "pipeline.cycle", "orchestrator"
        if any(word in message for word in ("report", "summary")):
            return "pipeline.report", "orchestrator"
        return "pipeline.health", "orchestrator"

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {key: WorkflowRunner._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [WorkflowRunner._jsonable(item) for item in value]
        return value
