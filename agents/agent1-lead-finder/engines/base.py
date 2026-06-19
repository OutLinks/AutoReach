"""Abstract base for all pipeline engines."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseEngine(ABC):
    """
    Base class for all four pipeline engines.

    Provides shared helpers: async semaphore, graceful error handling,
    and a standard logging interface. Subclasses implement `run()`.
    """

    def __init__(self, concurrency: int = 5) -> None:
        self._sem = asyncio.Semaphore(concurrency)

    async def _guarded(self, coro: Any) -> Any:
        """Run a coroutine under the concurrency semaphore."""
        async with self._sem:
            return await coro

    @staticmethod
    async def _retry(coro_fn: Any, *args: Any, retries: int = 2,
                     delay: float = 1.0, **kwargs: Any) -> Any:
        """
        Retry a coroutine with exponential backoff.
        Returns None after all retries are exhausted.
        """
        for attempt in range(retries + 1):
            try:
                return await coro_fn(*args, **kwargs)
            except Exception as exc:
                if attempt == retries:
                    logger.warning("Gave up after %d retries: %s", retries, exc)
                    return None
                wait = delay * (2 ** attempt)
                logger.debug("Retry %d/%d in %.1fs: %s", attempt + 1, retries, wait, exc)
                await asyncio.sleep(wait)
        return None
