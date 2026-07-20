"""Small in-process scheduler that creates durable hourly tick jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .executor import JobExecutor

logger = logging.getLogger(__name__)


class HourlyScheduler:
    def __init__(self, executor: JobExecutor, timezone_name: str, interval_seconds: int) -> None:
        self._executor = executor
        self._timezone = ZoneInfo(timezone_name)
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="autoreach-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            now = datetime.now(self._timezone)
            if now.hour in self._executor.orchestrator.config.schedule.plan:
                key = f"scheduled-tick:{now.date().isoformat()}:{now.hour:02d}"
                job = self._executor.submit(
                    "pipeline.tick", {"now": now.isoformat()}, dedupe_key=key
                )
                logger.info("Scheduled tick represented by job %s", job.id)
            await asyncio.sleep(self._interval)
