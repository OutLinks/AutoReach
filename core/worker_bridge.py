"""Synchronous client for the container-only Worker storage bridge."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class WorkerBridgeError(RuntimeError):
    pass


class WorkerBridgeClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (
            base_url or os.getenv("AUTOREACH_D1_BRIDGE_URL", "http://autoreach.storage")
        ).rstrip("/")

    def call(self, namespace: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}/v1/{namespace}/{operation}",
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:  # nosec B310 Worker virtual host
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            raise WorkerBridgeError(
                f"Worker bridge {namespace}/{operation} failed ({exc.code}): {detail}"
            ) from exc
        if not isinstance(value, dict):
            raise WorkerBridgeError(f"Worker bridge {namespace}/{operation} returned invalid JSON")
        return value
