"""One-at-a-time execution for durable API jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from orchestrator import Orchestrator
from orchestrator.adapters.live import _load_agent
from orchestrator.state_machine import STAGE_BY_NAME

from .jobs import JobRecord, JobStore
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
        store: JobStore,
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
                job = self.store.get(job_id)
                if job.status != "queued":
                    continue
                self.store.mark_running(job_id)
                result = await self._execute(job)
                self.store.mark_succeeded(job_id, _jsonable(result))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("API job %s failed", job_id)
                self.store.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()

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
            return await self.orchestrator.tick(datetime.fromisoformat(payload["now"]))
        if job.kind == "sender.event":
            result = self._handle_sender_event(payload)
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

    def _handle_sender_event(self, payload: dict[str, Any]) -> Any:
        if self._sender is None:
            module = _load_agent("agent4-sender", "agent4_sender")
            config = module.ServiceConfig.from_env()
            config.db_path = self.orchestrator.config.db_path
            config.emails_db_path = self.orchestrator.config.db_path
            self._sender = module.SenderAgent(config)
        sent_id = payload["sent_email_id"]
        event = payload["event"]
        if event == "reply":
            return self._sender.handle_reply(sent_id, payload.get("detail", ""))
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
