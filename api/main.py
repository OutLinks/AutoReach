"""FastAPI entrypoint for the deployable AutoReach backend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from orchestrator import Orchestrator, OrchestratorConfig
from orchestrator.models import DISCOVERED, PipelineLead
from orchestrator.state_machine import STAGE_BY_NAME

from .config_store import ConfigStore, DatabaseLocator
from .executor import JobExecutor
from .jobs import JobRecord, JobStore
from .scheduler import HourlyScheduler
from .settings import AppSettings
from .workflows import WorkflowStore

logger = logging.getLogger(__name__)

AGENT_STAGES: dict[str, tuple[str, ...]] = {
    "agent1-lead-finder": ("find",),
    "agent2-research-analyst": ("research",),
    "agent3-email-writer": ("write",),
    "agent4-sender": ("send", "followup"),
    "agent5-reply-handler": ("reply",),
}

AGENT_DESCRIPTIONS: dict[str, str] = {
    "agent1-lead-finder": "Discovers and verifies new leads.",
    "agent2-research-analyst": "Researches and enriches discovered leads.",
    "agent3-email-writer": "Creates personalized outreach emails.",
    "agent4-sender": "Sends prepared emails and processes follow-ups.",
    "agent5-reply-handler": "Classifies replies and selects the next action.",
}


class CampaignCreateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=20_000)


class SenderEventRequest(BaseModel):
    event: Literal["reply", "bounce", "complaint", "open", "click"]
    sent_email_id: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=10_000)
    bounce_type: Literal["hard", "soft"] = "hard"
    url: str = Field(default="", max_length=4_000)


class SetupRequest(BaseModel):
    database_path: str = Field(min_length=1, max_length=4_000)
    settings: dict[str, Any] = Field(default_factory=dict)


class SettingsUpdateRequest(BaseModel):
    values: dict[str, Any]


class AgentRunRequest(BaseModel):
    stage: str | None = None


class AcceptedJob(BaseModel):
    job_id: str


class ResearchSection(BaseModel):
    heading: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100_000)


class ResearchSource(BaseModel):
    title: str = Field(min_length=1, max_length=1_000)
    url: str = Field(min_length=1, max_length=4_000)


class ResearchCreateRequest(BaseModel):
    lead_id: str | None = Field(default=None, min_length=1, max_length=200)
    company: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str | None = Field(default=None, max_length=20_000)


class ResearchPatchRequest(BaseModel):
    lead_id: str | None = Field(default=None, min_length=1, max_length=200)
    company: str | None = Field(default=None, min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=100_000)
    sections: list[ResearchSection] | None = None
    sources: list[ResearchSource] | None = None


class ResearchIterateRequest(BaseModel):
    instructions: str = Field(min_length=1, max_length=20_000)


class EmailDraftCreateRequest(BaseModel):
    lead_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=200)
    tone: str | None = Field(default=None, max_length=100)
    instructions: str | None = Field(default=None, max_length=20_000)


class EmailDraftPatchRequest(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=1_000)
    body: str | None = Field(default=None, min_length=1, max_length=100_000)


class EmailRegenerateRequest(BaseModel):
    instructions: str | None = Field(default=None, max_length=20_000)


class MailboxReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=100_000)
    subject: str | None = Field(default=None, min_length=1, max_length=1_000)


class LeadSearchFilters(BaseModel):
    industry: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    size: str | None = Field(default=None, max_length=100)


class LeadSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=20_000)
    filters: LeadSearchFilters = Field(default_factory=LeadSearchFilters)
    limit: int = Field(default=25, ge=1, le=200)


class LeadImportRequest(BaseModel):
    lead_ids: list[str] = Field(min_length=1, max_length=500)


class OrchestratorMessageContext(BaseModel):
    campaign_id: str | None = Field(default=None, max_length=200)
    lead_id: str | None = Field(default=None, max_length=200)


class OrchestratorMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    context: OrchestratorMessageContext | None = None


def _validation_error(field: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[{"loc": ["body", field], "msg": message}],
    )


def create_app(
    settings: AppSettings | None = None,
    orchestrator_factory: Callable[[OrchestratorConfig], Orchestrator] = Orchestrator,
) -> FastAPI:
    settings = settings or AppSettings.from_env()
    settings.validate()

    async def stop_runtime(application: FastAPI) -> None:
        scheduler = getattr(application.state, "scheduler", None)
        executor = getattr(application.state, "executor", None)
        orchestrator = getattr(application.state, "orchestrator", None)
        job_store = getattr(application.state, "job_store", None)
        workflow_store = getattr(application.state, "workflow_store", None)
        if scheduler is not None:
            await scheduler.stop()
        if executor is not None:
            await executor.stop()
        if orchestrator is not None:
            orchestrator.store.close()
        if job_store is not None:
            job_store.close()
        if workflow_store is not None:
            workflow_store.close()

    async def start_runtime(
        application: FastAPI,
        database_path: Path,
        config_store: ConfigStore,
    ) -> None:
        config_store.apply_to_process()
        config = OrchestratorConfig.from_env()
        config.db_path = str(database_path)
        orchestrator = orchestrator_factory(config)
        job_store = JobStore(database_path)
        workflow_store = WorkflowStore(database_path)
        executor = JobExecutor(job_store, orchestrator, workflow_store)
        scheduler = HourlyScheduler(
            executor,
            config_store.get("scheduler_timezone"),
            settings.scheduler_interval_seconds,
        )
        application.state.database_path = database_path
        application.state.config_store = config_store
        application.state.orchestrator = orchestrator
        application.state.job_store = job_store
        application.state.workflow_store = workflow_store
        application.state.executor = executor
        application.state.scheduler = scheduler
        await executor.start()
        if config_store.get_bool("scheduler_enabled"):
            scheduler.start()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = settings
        application.state.runtime_lock = asyncio.Lock()
        application.state.database_locator = DatabaseLocator(settings.data_dir)
        database_path = application.state.database_locator.selected_path()
        config_store = ConfigStore(database_path)
        await start_runtime(application, database_path, config_store)
        try:
            yield
        finally:
            await stop_runtime(application)
            application.state.config_store.close()

    application = FastAPI(
        title="AutoReach API",
        version="0.1.0",
        description=(
            "API-only control plane for campaign planning, the AutoReach orchestrator, "
            "and its agents. Long-running operations are represented as durable jobs. "
            "Authentication is not enabled."
        ),
        lifespan=lifespan,
    )
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Content-Type"],
        )

    @application.get("/")
    async def root() -> dict[str, Any]:
        return {
            "name": "AutoReach API",
            "version": application.version,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/healthz",
            "agents": "/v1/agents",
            "research": "/v1/research",
            "email_writing": "/v1/emails/drafts",
            "mailbox": "/v1/mailbox/threads",
            "lead_finding": "/v1/lead-finding/searches",
            "orchestrator": {
                "cycle": "/v1/orchestrator/cycle",
                "health": "/v1/orchestrator/health",
                "report": "/v1/orchestrator/report",
                "messages": "/v1/orchestrator/messages",
            },
            "authentication": "disabled",
        }

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/setup")
    async def setup_status() -> dict[str, Any]:
        config_store: ConfigStore = application.state.config_store
        return {
            "configured": config_store.configured,
            "database_engine": "sqlite",
            "default_database_path": (
                ""
                if config_store.configured
                else str(application.state.database_locator.selected_path())
            ),
        }

    @application.post("/v1/setup", status_code=status.HTTP_201_CREATED)
    async def setup(request: SetupRequest) -> dict[str, Any]:
        async with application.state.runtime_lock:
            current_store: ConfigStore = application.state.config_store
            if current_store.configured:
                raise HTTPException(status_code=409, detail="AutoReach is already configured")
            locator: DatabaseLocator = application.state.database_locator
            try:
                selected = locator.validate(request.database_path)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            try:
                config_store = ConfigStore(selected)
                config_store.initialize(request.settings)
            except ValueError as exc:
                config_store.close()
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await stop_runtime(application)
            current_store.close()
            locator.save(selected)
            await start_runtime(application, selected, config_store)
            return {"configured": True, "database_path": str(selected)}

    @application.get("/v1/settings")
    async def get_settings() -> dict[str, Any]:
        return application.state.config_store.public_settings()

    @application.patch("/v1/settings")
    async def update_settings(request: SettingsUpdateRequest) -> dict[str, Any]:
        async with application.state.runtime_lock:
            config_store: ConfigStore = application.state.config_store
            try:
                config_store.update(request.values)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await stop_runtime(application)
            await start_runtime(application, application.state.database_path, config_store)
            return config_store.public_settings()

    @application.get("/v1/config")
    async def public_config() -> dict[str, Any]:
        orchestrator = application.state.orchestrator
        config_store: ConfigStore = application.state.config_store
        return {
            "environment": settings.environment,
            "simulate": orchestrator.config.simulate,
            "reply_handling_enabled": orchestrator.config.reply_handling_enabled,
            "scheduler_enabled": config_store.get_bool("scheduler_enabled"),
            "scheduler_timezone": config_store.get("scheduler_timezone"),
            "database_engine": "sqlite",
            "stages": list(STAGE_BY_NAME),
        }

    @application.post(
        "/v1/campaigns",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_campaign(request: CampaignCreateRequest) -> JobRecord:
        return application.state.executor.submit("campaign.create", {"prompt": request.prompt})

    @application.get("/v1/campaigns")
    async def list_campaigns(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        campaigns = application.state.orchestrator.store.list_campaigns(limit, offset)
        return {"items": [item.model_dump(mode="json") for item in campaigns]}

    @application.get("/v1/campaigns/{campaign_id}")
    async def get_campaign(campaign_id: str) -> Any:
        campaign = application.state.orchestrator.store.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return campaign

    @application.post("/v1/campaigns/{campaign_id}/activate")
    async def activate_campaign(campaign_id: str) -> Any:
        try:
            return application.state.orchestrator.activate_campaign(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Campaign not found") from exc

    @application.post(
        "/v1/jobs/find",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_find() -> JobRecord:
        return application.state.executor.submit("pipeline.find")

    @application.post(
        "/v1/jobs/cycle",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_cycle() -> JobRecord:
        return application.state.executor.submit("pipeline.cycle")

    @application.post(
        "/v1/jobs/stages/{stage_name}",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_stage(stage_name: str) -> JobRecord:
        if stage_name not in STAGE_BY_NAME:
            raise HTTPException(status_code=404, detail="Unknown pipeline stage")
        return application.state.executor.submit("pipeline.stage", {"stage": stage_name})

    @application.get("/v1/jobs")
    async def list_jobs(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return {"items": application.state.job_store.list(limit, offset)}

    @application.get("/v1/jobs/{job_id}", response_model=JobRecord)
    async def get_job(job_id: str) -> JobRecord:
        try:
            return application.state.job_store.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @application.get("/v1/leads")
    async def list_leads(
        lead_state: Annotated[str | None, Query(alias="state")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        leads = application.state.orchestrator.store.all_leads()
        if lead_state:
            leads = [lead for lead in leads if lead.state == lead_state]
        total = len(leads)
        return {
            "items": [lead.model_dump(mode="json") for lead in leads[offset : offset + limit]],
            "total": total,
        }

    # ── Interactive research ──────────────────────────────────────────────────

    @application.post(
        "/v1/research",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_research(request: ResearchCreateRequest) -> AcceptedJob:
        if not request.lead_id and not (request.company or "").strip():
            raise _validation_error("lead_id", "Provide lead_id or company")
        if request.lead_id:
            lead = application.state.orchestrator.store.get_lead(request.lead_id)
            if lead is None:
                raise HTTPException(status_code=404, detail="Lead not found")
        job = application.state.executor.submit(
            "research.create",
            request.model_dump(exclude_none=True),
        )
        return AcceptedJob(job_id=job.id)

    @application.get("/v1/research")
    async def list_research(lead_id: str | None = None) -> dict[str, Any]:
        return {
            "items": application.state.workflow_store.list_research(lead_id)
        }

    @application.get("/v1/research/{research_id}")
    async def get_research(research_id: str) -> dict[str, Any]:
        try:
            return application.state.workflow_store.get_research(research_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Research document not found") from exc

    @application.patch("/v1/research/{research_id}")
    async def update_research(
        research_id: str,
        request: ResearchPatchRequest,
    ) -> dict[str, Any]:
        values = request.model_dump(exclude_none=True, mode="json")
        if values.get("lead_id") and (
            application.state.orchestrator.store.get_lead(values["lead_id"]) is None
        ):
            raise HTTPException(status_code=404, detail="Lead not found")
        try:
            return application.state.workflow_store.update_research(
                research_id,
                values,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Research document not found") from exc

    @application.post(
        "/v1/research/{research_id}/iterate",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def iterate_research(
        research_id: str,
        request: ResearchIterateRequest,
    ) -> AcceptedJob:
        try:
            application.state.workflow_store.get_research(research_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Research document not found") from exc
        job = application.state.executor.submit(
            "research.iterate",
            {"research_id": research_id, "instructions": request.instructions},
        )
        return AcceptedJob(job_id=job.id)

    # ── Editable email drafts ─────────────────────────────────────────────────

    @application.post(
        "/v1/emails/drafts",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_email_draft(request: EmailDraftCreateRequest) -> AcceptedJob:
        if application.state.orchestrator.store.get_lead(request.lead_id) is None:
            raise HTTPException(status_code=404, detail="Lead not found")
        if application.state.orchestrator.store.get_campaign(request.campaign_id) is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        job = application.state.executor.submit(
            "email.generate",
            request.model_dump(exclude_none=True),
        )
        return AcceptedJob(job_id=job.id)

    @application.get("/v1/emails/drafts")
    async def list_email_drafts(
        lead_id: str | None = None,
        campaign_id: str | None = None,
        draft_status: Annotated[
            Literal["draft", "approved", "sent"] | None,
            Query(alias="status"),
        ] = None,
    ) -> dict[str, Any]:
        return {
            "items": application.state.workflow_store.list_drafts(
                lead_id=lead_id,
                campaign_id=campaign_id,
                status=draft_status,
            )
        }

    @application.get("/v1/emails/drafts/{draft_id}")
    async def get_email_draft(draft_id: str) -> dict[str, Any]:
        try:
            return application.state.workflow_store.get_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Email draft not found") from exc

    @application.patch("/v1/emails/drafts/{draft_id}")
    async def update_email_draft(
        draft_id: str,
        request: EmailDraftPatchRequest,
    ) -> dict[str, Any]:
        try:
            return application.state.workflow_store.update_draft(
                draft_id,
                request.model_dump(exclude_none=True),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Email draft not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post(
        "/v1/emails/drafts/{draft_id}/regenerate",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def regenerate_email_draft(
        draft_id: str,
        request: EmailRegenerateRequest,
    ) -> AcceptedJob:
        try:
            application.state.workflow_store.get_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Email draft not found") from exc
        job = application.state.executor.submit(
            "email.regenerate",
            {
                "draft_id": draft_id,
                **request.model_dump(exclude_none=True),
            },
        )
        return AcceptedJob(job_id=job.id)

    @application.post("/v1/emails/drafts/{draft_id}/approve")
    async def approve_email_draft(draft_id: str) -> dict[str, Any]:
        try:
            draft = application.state.workflow_store.get_draft(draft_id)
            if draft["status"] == "sent":
                raise HTTPException(status_code=409, detail="Email has already been sent")
            return application.state.workflow_store.set_draft_status(
                draft_id,
                "approved",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Email draft not found") from exc

    @application.post(
        "/v1/emails/drafts/{draft_id}/send",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def send_email_draft(draft_id: str) -> AcceptedJob:
        try:
            draft = application.state.workflow_store.get_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Email draft not found") from exc
        if draft["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail="Email draft must be approved before sending",
            )
        job = application.state.executor.submit(
            "email.send",
            {"draft_id": draft_id},
        )
        return AcceptedJob(job_id=job.id)

    # ── Unified mailbox ───────────────────────────────────────────────────────

    @application.get("/v1/mailbox/threads")
    async def list_mailbox_threads(
        folder: Literal["inbox", "sent", "replied", "bounced"] = "inbox",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        application.state.executor.workflow_runner.sync_mailbox_history()
        return application.state.workflow_store.list_mailbox_threads(
            folder,
            page,
            page_size,
        )

    @application.get("/v1/mailbox/threads/{thread_id}")
    async def get_mailbox_thread(thread_id: str) -> dict[str, Any]:
        application.state.executor.workflow_runner.sync_mailbox_history()
        try:
            return application.state.workflow_store.get_mailbox_thread(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mailbox thread not found") from exc

    @application.post(
        "/v1/mailbox/threads/{thread_id}/reply",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def reply_to_mailbox_thread(
        thread_id: str,
        request: MailboxReplyRequest,
    ) -> AcceptedJob:
        application.state.executor.workflow_runner.sync_mailbox_history()
        try:
            application.state.workflow_store.get_mailbox_thread(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mailbox thread not found") from exc
        job = application.state.executor.submit(
            "mailbox.reply",
            {"thread_id": thread_id, **request.model_dump(exclude_none=True)},
        )
        return AcceptedJob(job_id=job.id)

    @application.post("/v1/mailbox/threads/{thread_id}/mark-read")
    async def mark_mailbox_thread_read(thread_id: str) -> dict[str, bool]:
        try:
            application.state.workflow_store.mark_thread_read(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mailbox thread not found") from exc
        return {"read": True}

    # ── Lead-finding search and selective import ──────────────────────────────

    @application.post(
        "/v1/lead-finding/searches",
        response_model=AcceptedJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_lead_search(request: LeadSearchRequest) -> AcceptedJob:
        search = application.state.workflow_store.create_search(
            request.query,
            request.filters.model_dump(exclude_none=True),
            request.limit,
        )
        job = application.state.executor.submit(
            "lead_search.run",
            {"search_id": search["id"], "limit": request.limit},
        )
        return AcceptedJob(job_id=job.id)

    @application.get("/v1/lead-finding/searches")
    async def list_lead_searches() -> dict[str, Any]:
        return {"items": application.state.workflow_store.list_searches()}

    @application.get("/v1/lead-finding/searches/{search_id}")
    async def get_lead_search(search_id: str) -> dict[str, Any]:
        try:
            return application.state.workflow_store.get_search(search_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Lead search not found") from exc

    @application.post("/v1/lead-finding/searches/{search_id}/import")
    async def import_lead_search_results(
        search_id: str,
        request: LeadImportRequest,
    ) -> dict[str, Any]:
        lead_ids = list(dict.fromkeys(request.lead_ids))
        try:
            search = application.state.workflow_store.get_search(search_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Lead search not found") from exc
        if search["status"] != "succeeded":
            raise HTTPException(status_code=409, detail="Lead search is not complete")
        results = application.state.workflow_store.search_results(search_id, lead_ids)
        found_ids = {result["id"] for result in results}
        missing = [lead_id for lead_id in lead_ids if lead_id not in found_ids]
        if missing:
            raise _validation_error(
                "lead_ids",
                f"Unknown search result lead IDs: {', '.join(missing)}",
            )
        imported: list[str] = []
        for result in results:
            lead_id = result["id"]
            existing = application.state.orchestrator.store.get_lead(lead_id)
            if existing is None:
                application.state.orchestrator.store.save_artifact(
                    artifact_id=f"lead-search:{search_id}:{lead_id}",
                    kind="lead_discovery",
                    lead_id=lead_id,
                    source_job=search_id,
                    payload=result,
                )
                application.state.orchestrator.store.upsert_lead(
                    PipelineLead(
                        id=lead_id,
                        state=DISCOVERED,
                        email=result.get("email") or "",
                        company=result.get("company_name") or "",
                        industry=result.get("industry") or "",
                        quality_score=(result.get("lead_score") or 0.0) / 100.0,
                        discovered_at=datetime.now(timezone.utc),
                        source_job=search_id,
                        metadata={
                            "lead_search_id": search_id,
                            "source_preview": result,
                        },
                    )
                )
            imported.append(lead_id)
        application.state.workflow_store.mark_search_results_imported(
            search_id,
            imported,
        )
        return {"imported_count": len(imported), "lead_ids": imported}

    # ── Natural-language orchestrator ─────────────────────────────────────────

    @application.post(
        "/v1/orchestrator/messages",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_orchestrator_message(
        request: OrchestratorMessageRequest,
    ) -> dict[str, str]:
        context = request.context.model_dump(exclude_none=True) if request.context else {}
        if context.get("campaign_id") and (
            application.state.orchestrator.store.get_campaign(context["campaign_id"])
            is None
        ):
            raise HTTPException(status_code=404, detail="Campaign not found")
        if context.get("lead_id") and (
            application.state.orchestrator.store.get_lead(context["lead_id"]) is None
        ):
            raise HTTPException(status_code=404, detail="Lead not found")
        conversation_id = application.state.workflow_store.create_conversation(
            request.message
        )
        job = application.state.executor.submit(
            "orchestrator.message",
            {
                "message": request.message,
                "context": context,
                "conversation_id": conversation_id,
            },
        )
        return {"job_id": job.id, "conversation_id": conversation_id}

    @application.get("/v1/orchestrator/conversations")
    async def list_orchestrator_conversations() -> dict[str, Any]:
        return {"items": application.state.workflow_store.list_conversations()}

    @application.get("/v1/orchestrator/conversations/{conversation_id}")
    async def get_orchestrator_conversation(conversation_id: str) -> dict[str, Any]:
        try:
            return application.state.workflow_store.get_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="Orchestrator conversation not found",
            ) from exc

    @application.get("/v1/orchestrator/conversations/{conversation_id}/timeline")
    async def get_orchestrator_timeline(
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        try:
            return application.state.workflow_store.conversation_timeline(
                conversation_id
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="Orchestrator conversation not found",
            ) from exc

    @application.get("/v1/health")
    async def pipeline_health() -> Any:
        return application.state.orchestrator.health()

    @application.get("/v1/report")
    async def pipeline_report() -> Any:
        return application.state.orchestrator.report()

    @application.post(
        "/v1/events/sender",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def sender_event(request: SenderEventRequest) -> JobRecord:
        """Accept a normalized event after a provider-specific signature check upstream."""
        return application.state.executor.submit("sender.event", request.model_dump())

    @application.get("/v1/agents")
    async def list_agents() -> dict[str, Any]:
        return {
            "items": [
                {
                    "name": name,
                    "description": AGENT_DESCRIPTIONS[name],
                    "stages": list(stages),
                    "run_endpoint": f"/v1/agents/{name}/run",
                }
                for name, stages in AGENT_STAGES.items()
            ]
        }

    @application.post(
        "/v1/agents/{agent_name}/run",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_agent(agent_name: str, request: AgentRunRequest) -> JobRecord:
        stages = AGENT_STAGES.get(agent_name)
        if stages is None:
            raise HTTPException(status_code=404, detail="Unknown agent")
        if request.stage is None:
            if len(stages) != 1:
                raise HTTPException(
                    status_code=422,
                    detail=f"Choose one of these stages: {', '.join(stages)}",
                )
            stage_name = stages[0]
        else:
            stage_name = request.stage
            if stage_name not in stages:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{agent_name} supports these stages: {', '.join(stages)}"
                    ),
                )
        return application.state.executor.submit(
            "pipeline.stage",
            {"stage": stage_name, "agent": agent_name},
        )

    @application.post(
        "/v1/orchestrator/cycle",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_orchestrator_cycle() -> JobRecord:
        return application.state.executor.submit("pipeline.cycle")

    @application.get("/v1/orchestrator/health")
    async def orchestrator_health() -> Any:
        return application.state.orchestrator.health()

    @application.get("/v1/orchestrator/report")
    async def orchestrator_report() -> Any:
        return application.state.orchestrator.report()

    return application


app = create_app()
