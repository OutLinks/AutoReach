"""Authentication helpers for Worker-to-container control-plane calls."""

from __future__ import annotations

import hashlib
import hmac
import os
import time


_MAX_CLOCK_SKEW_SECONDS = 300


def _payload(job_id: str, timestamp: str) -> bytes:
    return f"POST\\n/internal/jobs/{job_id}/execute\\n{timestamp}".encode("utf-8")


def sign_job_execution(secret: str, job_id: str, timestamp: str) -> str:
    """Return the Worker-compatible HMAC for an internal job execution call."""
    return hmac.new(
        secret.encode("utf-8"),
        _payload(job_id, timestamp),
        hashlib.sha256,
    ).hexdigest()


def validate_job_execution(
    job_id: str,
    timestamp: str | None,
    signature: str | None,
    *,
    now: float | None = None,
) -> bool:
    """Validate a short-lived HMAC using the container-only shared secret."""
    secret = os.getenv("AUTOREACH_INTERNAL_HMAC_SECRET", "")
    if not secret or not timestamp or not signature:
        return False
    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    current_time = time.time() if now is None else now
    if abs(current_time - request_time) > _MAX_CLOCK_SKEW_SECONDS:
        return False
    expected = sign_job_execution(secret, job_id, timestamp)
    return hmac.compare_digest(expected, signature)
