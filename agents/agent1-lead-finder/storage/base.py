"""Durable pipeline-state contract shared by Redis and D1 adapters."""

from __future__ import annotations

from typing import Any, Protocol


class LeadPipelineStore(Protocol):
    """Per-job stage state plus cross-job contact deduplication."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def save_job(self, job: Any) -> None: ...

    async def load_job(self, job_id: str) -> dict[str, Any] | None: ...

    async def push_leads(self, job_id: str, stage: str, leads: list[Any]) -> None: ...

    async def pull_leads(self, job_id: str, stage: str) -> list[dict[str, Any]]: ...

    async def is_duplicate(
        self, email: str | None, linkedin_url: str | None, name_key: str | None
    ) -> bool: ...

    async def register_lead(
        self, email: str | None, linkedin_url: str | None, name_key: str | None
    ) -> None: ...

    async def clear_job(self, job_id: str) -> None: ...
