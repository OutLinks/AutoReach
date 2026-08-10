"""One-at-a-time execution for durable API jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from pydantic import BaseModel

from orchestrator import Orchestrator
from orchestrator import models as orchestrator_models
from orchestrator.adapters.live import _load_agent
from orchestrator.state_machine import STAGE_BY_NAME

from .jobs import JobRecord, JobRepository
from .workflow_runner import WorkflowRunner
from .workflows import WorkflowStore

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class JobExecutor:
    """Runs jobs sequentially so the file/SQLite agent contracts stay safe."""

    def __init__(
        self,
        store: JobRepository,
        orchestrator: Orchestrator,
        workflows: WorkflowStore,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.workflows = workflows
        self.workflow_runner = WorkflowRunner(workflows, orchestrator)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._sender: Any | None = None

    async def start(self) -> None:
        if self._worker is not None:
            return
        for job_id in self.store.recover_incomplete():
            self._queue.put_nowait(job_id)
        self._worker = asyncio.create_task(self._run(), name="autoreach-job-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        if self._sender is not None:
            self._sender.close()
            self._sender = None
        self.workflow_runner.close()

    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> JobRecord:
        job, created = self.store.create(kind, payload, dedupe_key)
        if created:
            self._queue.put_nowait(job.id)
        return job

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self.execute_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("API job %s failed", job_id)
            finally:
                self._queue.task_done()

    async def execute_job(self, job_id: str) -> JobRecord:
        """Execute one queued job and persist its terminal result.

        This is the production-facing execution seam: a Queue consumer can
        invoke it through the authenticated internal HTTP endpoint instead of
        relying on this process's in-memory queue. The current SQLite store is
        retained for local development only; its D1 replacement will add durable
        lease expiry and retry scheduling with this same method contract.
        """
        job = self.store.claim_for_execution(job_id)
        if job is None:
            return self.store.get(job_id)
        try:
            result = await self._execute(job)
        except Exception as exc:
            logger.exception("API job %s failed", job_id)
            self.store.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
            return self.store.get(job_id)
        self.store.mark_succeeded(job_id, _jsonable(result))
        return self.store.get(job_id)

    async def _execute(self, job: JobRecord) -> Any:
        payload = job.payload
        if job.kind == "campaign.create":
            return await self.orchestrator.create_campaign(payload["prompt"])
        if job.kind == "pipeline.find":
            return await self.orchestrator.run_find()
        if job.kind == "pipeline.cycle":
            return await self.orchestrator.run_cycle()
        if job.kind == "pipeline.stage":
            stage = STAGE_BY_NAME[payload["stage"]]
            return await self.orchestrator.run_stage(stage)
        if job.kind == "pipeline.tick":
            tick = datetime.fromisoformat(payload["now"])
            if payload.get("timezone"):
                tick = tick.astimezone(ZoneInfo(payload["timezone"]))
            return await self.orchestrator.tick(tick)
        if job.kind == "sender.followup":
            sender = self._sender_agent()
            result = await sender.run_scheduled_followup(
                lead_id=payload["lead_id"],
                expected_step=payload["expected_step"],
                job_id=job.id,
                now=datetime.now(timezone.utc),
            )
            self._reconcile_followup_lifecycle(payload["lead_id"], sender)
            return result
        if job.kind == "sender.event":
            result = await self._handle_sender_event(payload)
            self.workflow_runner.record_sender_event(payload, result, job.id)
            return result
        if job.kind in {
            "research.create",
            "research.iterate",
            "email.generate",
            "email.regenerate",
            "email.send",
            "mailbox.reply",
            "lead_search.run",
            "orchestrator.message",
        }:
            return await self.workflow_runner.execute(job.kind, payload, job.id)
        raise ValueError(f"Unsupported job kind: {job.kind}")

    async def _handle_sender_event(self, payload: dict[str, Any]) -> Any:
        self._sender_agent()
        sent_id = payload["sent_email_id"]
        event = payload["event"]
        if event == "reply":
            notification = self._sender.handle_reply(
                sent_id, payload.get("detail", ""), payload.get("provider_event_id", "")
            )
            if notification is None:
                return None
            result: dict[str, Any] = {"notification": notification.model_dump(mode="json")}
            lead = self.orchestrator.store.get_lead(notification.lead_id)
            if lead:
                previous = lead.state
                lead.state = orchestrator_models.REPLIED
                lead.replied_at = datetime.now(timezone.utc)
                self.orchestrator.store.upsert_lead(lead)
                self.orchestrator.store.log_event(lead.id, previous, lead.state, "sender:reply")
            if self.orchestrator.config.reply_handling_enabled:
                module = _load_agent("agent5-reply-handler", "agent5_reply_handler")
                config = module.ServiceConfig.from_env()
                config.enabled = True
                handler = module.ReplyHandlerAgent(config)
                reply_job = await handler.handle_payload({
                    **notification.model_dump(mode="json"),
                    "provider_event_id": payload.get("provider_event_id", ""),
                })
                result["reply_job"] = reply_job.model_dump(mode="json")
                status = handler.conversation_status(notification.lead_id)
                if lead and status:
                    target = {
                        "meeting_booked": orchestrator_models.MEETING_BOOKED,
                        "closed": orchestrator_models.CLOSED,
                    }.get(status)
                    if target is None and reply_job.handled and status not in {"active", "escalated"}:
                        target = orchestrator_models.HANDLED
                    if target:
                        previous = lead.state
                        lead.state = target
                        self.orchestrator.store.upsert_lead(lead)
                        self.orchestrator.store.log_event(lead.id, previous, target, f"reply:{status}")
                handler.close()
            return result
        if event == "bounce":
            return {
                "disposition": self._sender.handle_bounce(
                    sent_id,
                    payload.get("bounce_type", "hard"),
                    payload.get("detail", ""),
                )
            }
        if event == "complaint":
            return {"recorded": self._sender.handle_complaint(sent_id, payload.get("detail", ""))}
        if event == "open":
            self._sender.record_open(sent_id, payload.get("detail", ""))
            return {"recorded": True}
        if event == "click":
            self._sender.record_click(sent_id, payload.get("url", ""))
            return {"recorded": True}
        raise ValueError(f"Unsupported sender event: {event}")

    def _sender_agent(self) -> Any:
        if self._sender is None:
            module = _load_agent("agent4-sender", "agent4_sender")
            config = module.ServiceConfig.from_env()
            config.db_path = self.orchestrator.config.db_path
            config.emails_db_path = self.orchestrator.config.db_path
            self._sender = module.SenderAgent(config)
        return self._sender

    def _reconcile_followup_lifecycle(self, lead_id: str, sender: Any) -> None:
        """Reflect durable sequence progress in the canonical lead lifecycle."""
        lead = self.orchestrator.store.get_lead(lead_id)
        sequence = sender.sequence_for(lead_id)
        if lead is None or sequence is None or lead.state in {
            orchestrator_models.REPLIED,
            orchestrator_models.HANDLED,
            orchestrator_models.MEETING_BOOKED,
            orchestrator_models.CLOSED,
            orchestrator_models.DEAD,
        }:
            return
        previous = lead.state
        if sequence.status == "completed":
            lead.state = orchestrator_models.CLOSED
            note = "sequence exhausted"
        elif sequence.is_active:
            lead.state = orchestrator_models.FOLLOWING_UP
            note = f"followup:{sequence.current_step}"
        else:
            return
        if lead.state != previous:
            self.orchestrator.store.upsert_lead(lead)
            self.orchestrator.store.log_event(lead.id, previous, lead.state, note)
