"""FastAPI entrypoint for the deployable AutoReach backend."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from orchestrator import Orchestrator, OrchestratorConfig
from orchestrator.state_machine import STAGE_BY_NAME

from .executor import JobExecutor
from .jobs import JobRecord, JobStore
from .scheduler import HourlyScheduler
from .settings import AppSettings

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class CampaignCreateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=20_000)


class SenderEventRequest(BaseModel):
    event: Literal["reply", "bounce", "complaint", "open", "click"]
    sent_email_id: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=10_000)
    bounce_type: Literal["hard", "soft"] = "hard"
    url: str = Field(default="", max_length=4_000)


def create_app(
    settings: AppSettings | None = None,
    orchestrator_factory: Callable[[OrchestratorConfig], Orchestrator] = Orchestrator,
) -> FastAPI:
    settings = settings or AppSettings.from_env()
    settings.validate()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        config = OrchestratorConfig.from_env()
        config.db_path = str(settings.data_dir / "orchestrator" / "orchestrator.db")
        orchestrator = orchestrator_factory(config)
        job_store = JobStore(settings.job_db_path)
        executor = JobExecutor(job_store, orchestrator)
        scheduler = HourlyScheduler(
            executor, settings.scheduler_timezone, settings.scheduler_interval_seconds
        )
        application.state.settings = settings
        application.state.orchestrator = orchestrator
        application.state.job_store = job_store
        application.state.executor = executor
        application.state.scheduler = scheduler
        await executor.start()
        if settings.scheduler_enabled:
            scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await executor.stop()
            orchestrator.store.close()
            job_store.close()

    application = FastAPI(
        title="AutoReach API",
        version="0.1.0",
        description=(
            "Authenticated API for campaign planning and the AutoReach agent pipeline. "
            "Long-running operations are represented as durable jobs."
        ),
        lifespan=lifespan,
    )
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    async def require_auth(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if not settings.api_secret and settings.environment != "production":
            return
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not secrets.compare_digest(credentials.credentials, settings.api_secret)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = Depends(require_auth)

    @application.get("/")
    async def root() -> dict[str, str]:
        return {"name": "AutoReach API", "docs": "/docs", "health": "/healthz"}

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/config", dependencies=[auth])
    async def public_config() -> dict[str, Any]:
        orchestrator = application.state.orchestrator
        return {
            "environment": settings.environment,
            "simulate": orchestrator.config.simulate,
            "reply_handling_enabled": orchestrator.config.reply_handling_enabled,
            "scheduler_enabled": settings.scheduler_enabled,
            "scheduler_timezone": settings.scheduler_timezone,
            "stages": list(STAGE_BY_NAME),
        }

    @application.post(
        "/v1/campaigns",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    async def create_campaign(request: CampaignCreateRequest) -> JobRecord:
        return application.state.executor.submit("campaign.create", {"prompt": request.prompt})

    @application.get("/v1/campaigns", dependencies=[auth])
    async def list_campaigns(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        campaigns = application.state.orchestrator.store.list_campaigns(limit, offset)
        return {"items": [item.model_dump(mode="json") for item in campaigns]}

    @application.get("/v1/campaigns/{campaign_id}", dependencies=[auth])
    async def get_campaign(campaign_id: str) -> Any:
        campaign = application.state.orchestrator.store.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return campaign

    @application.post("/v1/campaigns/{campaign_id}/activate", dependencies=[auth])
    async def activate_campaign(campaign_id: str) -> Any:
        try:
            return application.state.orchestrator.activate_campaign(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Campaign not found") from exc

    @application.post(
        "/v1/jobs/find",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    async def run_find() -> JobRecord:
        return application.state.executor.submit("pipeline.find")

    @application.post(
        "/v1/jobs/cycle",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    async def run_cycle() -> JobRecord:
        return application.state.executor.submit("pipeline.cycle")

    @application.post(
        "/v1/jobs/stages/{stage_name}",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    async def run_stage(stage_name: str) -> JobRecord:
        if stage_name not in STAGE_BY_NAME:
            raise HTTPException(status_code=404, detail="Unknown pipeline stage")
        return application.state.executor.submit("pipeline.stage", {"stage": stage_name})

    @application.get("/v1/jobs", dependencies=[auth])
    async def list_jobs(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return {"items": application.state.job_store.list(limit, offset)}

    @application.get("/v1/jobs/{job_id}", response_model=JobRecord, dependencies=[auth])
    async def get_job(job_id: str) -> JobRecord:
        try:
            return application.state.job_store.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @application.get("/v1/leads", dependencies=[auth])
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

    @application.get("/v1/health", dependencies=[auth])
    async def pipeline_health() -> Any:
        return application.state.orchestrator.health()

    @application.get("/v1/report", dependencies=[auth])
    async def pipeline_report() -> Any:
        return application.state.orchestrator.report()

    @application.post(
        "/v1/events/sender",
        response_model=JobRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    async def sender_event(request: SenderEventRequest) -> JobRecord:
        """Accept a normalized event after a provider-specific signature check upstream."""
        return application.state.executor.submit("sender.event", request.model_dump())

    return application


app = create_app()
