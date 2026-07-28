"""
Redis-backed pipeline store for Agent 1.

Redis acts as the message bus between engines. Each job gets its own
key namespace. Global deduplication sets persist across jobs.

Key schema:
    job:{job_id}:meta              → Hash   — SearchJob metadata
    job:{job_id}:leads:raw         → List   — raw Lead JSON strings (from search)
    job:{job_id}:leads:verified    → List   — verified Lead JSON strings
    job:{job_id}:leads:enriched    → List   — enriched Lead JSON strings
    job:{job_id}:leads:scored      → List   — scored Lead JSON strings (sorted by score)
    global:seen:emails             → Set    — all emails ever processed (cross-job dedup)
    global:seen:linkedin           → Set    — all LinkedIn URLs processed
    global:seen:names              → Set    — "company_name::full_name" combos
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel so we can import this module even without redis installed;
# errors surface only when RedisStore is actually instantiated.
try:
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError

    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False
    aioredis = None  # type: ignore
    RedisError = Exception  # type: ignore[misc,assignment]


class RedisStore:
    """
    Typed async Redis interface for the lead pipeline.

    All serialisation is JSON. TTL is applied per job — global dedup sets
    never expire so duplicate detection works across runs.
    """

    def __init__(self, url: str, ttl: int = 86400) -> None:
        if not _REDIS_AVAILABLE:
            raise RuntimeError(
                "redis package not installed. Run: pip install 'redis[asyncio]'"
            )
        self._url = url
        self._ttl = ttl
        self._client: Any = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = await aioredis.from_url(
                self._url, encoding="utf-8", decode_responses=True
            )
            try:
                await self._client.ping()
            except RedisError as exc:
                await self._client.aclose()
                self._client = None
                raise ConnectionError(
                    "Redis working queue is unavailable. Start the Redis service "
                    "and set redis_url with PATCH /v1/settings."
                ) from exc
            logger.debug("Redis connected: %s", self._url)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Job metadata ──────────────────────────────────────────────────────────

    async def save_job(self, job: Any) -> None:
        """Persist a SearchJob to Redis as a hash."""
        await self.connect()
        key = f"job:{job.id}:meta"
        await self._client.set(key, job.model_dump_json(), ex=self._ttl)
        logger.debug("Saved job %s  status=%s", job.id, job.status)

    async def load_job(self, job_id: str) -> dict[str, Any] | None:
        await self.connect()
        raw = await self._client.get(f"job:{job_id}:meta")
        return json.loads(raw) if raw else None

    # ── Lead lists ────────────────────────────────────────────────────────────

    async def push_leads(self, job_id: str, stage: str, leads: list[Any]) -> None:
        """Append a batch of Lead objects to the stage list."""
        await self.connect()
        if not leads:
            return
        key = f"job:{job_id}:leads:{stage}"
        pipe = self._client.pipeline()
        for lead in leads:
            pipe.rpush(key, lead.model_dump_json())
        pipe.expire(key, self._ttl)
        await pipe.execute()
        logger.debug("Pushed %d leads to %s", len(leads), key)

    async def pull_leads(self, job_id: str, stage: str) -> list[dict[str, Any]]:
        """Return all leads from a stage list as raw dicts."""
        await self.connect()
        key = f"job:{job_id}:leads:{stage}"
        raw_list = await self._client.lrange(key, 0, -1)
        return [json.loads(r) for r in raw_list]

    # ── Global deduplication sets ─────────────────────────────────────────────

    async def is_duplicate(self, email: str | None, linkedin_url: str | None,
                           name_key: str | None) -> bool:
        """
        Check if any dedup key has been seen before across all jobs.
        Returns True if ANY of the provided keys is already in the global sets.
        """
        await self.connect()
        checks: list[tuple[str, str]] = []
        if email:
            checks.append(("global:seen:emails", email.lower().strip()))
        if linkedin_url:
            checks.append(("global:seen:linkedin", linkedin_url.lower().strip()))
        if name_key:
            checks.append(("global:seen:names", name_key.lower().strip()))

        if not checks:
            return False

        pipe = self._client.pipeline()
        for set_key, value in checks:
            pipe.sismember(set_key, value)
        results = await pipe.execute()
        return any(results)

    async def register_lead(self, email: str | None, linkedin_url: str | None,
                            name_key: str | None) -> None:
        """Add a lead's dedup keys to the global seen sets."""
        await self.connect()
        pipe = self._client.pipeline()
        if email:
            pipe.sadd("global:seen:emails", email.lower().strip())
        if linkedin_url:
            pipe.sadd("global:seen:linkedin", linkedin_url.lower().strip())
        if name_key:
            pipe.sadd("global:seen:names", name_key.lower().strip())
        await pipe.execute()

    async def clear_job(self, job_id: str) -> None:
        """Delete all keys for a specific job (does not touch global dedup sets)."""
        await self.connect()
        pattern = f"job:{job_id}:*"
        keys = await self._client.keys(pattern)
        if keys:
            await self._client.delete(*keys)
            logger.debug("Cleared %d keys for job %s", len(keys), job_id)
