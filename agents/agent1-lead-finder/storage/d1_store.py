"""Cloudflare D1 implementation of Agent 1's durable pipeline contract."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class D1LeadPipelineStore:
    """Uses the container-only Worker outbound bridge; no D1 token is exposed."""

    def __init__(self, ttl: int = 86_400, bridge_url: str | None = None) -> None:
        self._ttl = ttl
        self._bridge_url = (
            bridge_url or os.getenv("AUTOREACH_D1_BRIDGE_URL", "http://autoreach.storage")
        ).rstrip("/")

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def save_job(self, job: Any) -> None:
        await self._call(
            "save-job",
            {
                "job_id": job.id,
                "job": job.model_dump(mode="json"),
                "expires_at": self._expires_at(),
            },
        )

    async def load_job(self, job_id: str) -> dict[str, Any] | None:
        return (await self._call("load-job", {"job_id": job_id})).get("job")

    async def push_leads(self, job_id: str, stage: str, leads: list[Any]) -> None:
        if not leads:
            return
        await self._call(
            "push-leads",
            {
                "job_id": job_id,
                "stage": stage,
                "leads": [lead.model_dump(mode="json") for lead in leads],
                "expires_at": self._expires_at(),
            },
        )

    async def pull_leads(self, job_id: str, stage: str) -> list[dict[str, Any]]:
        return list((await self._call("pull-leads", {"job_id": job_id, "stage": stage})).get("leads", []))

    async def is_duplicate(
        self, email: str | None, linkedin_url: str | None, name_key: str | None
    ) -> bool:
        return bool(
            (await self._call("is-duplicate", {"keys": self._keys(email, linkedin_url, name_key)}))
            .get("duplicate")
        )

    async def register_lead(
        self, email: str | None, linkedin_url: str | None, name_key: str | None
    ) -> None:
        await self._call("register-lead", {"keys": self._keys(email, linkedin_url, name_key)})

    async def clear_job(self, job_id: str) -> None:
        await self._call("clear-job", {"job_id": job_id})

    async def _call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._bridge_url}/v1/lead-pipeline/{operation}", json=payload
                )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            value = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError("D1 lead pipeline bridge is unavailable") from exc
        if not isinstance(value, dict):
            raise RuntimeError("D1 lead pipeline bridge returned an invalid response")
        return value

    def _expires_at(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=self._ttl)).isoformat()

    @staticmethod
    def _keys(email: str | None, linkedin_url: str | None, name_key: str | None) -> list[dict[str, str]]:
        values = (("email", email), ("linkedin", linkedin_url), ("name", name_key))
        return [
            {
                "type": kind,
                "hash": hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest(),
            }
            for kind, value in values
            if value and value.strip()
        ]
