"""D1-backed persistence adapter for Agent 4 inside a Cloudflare Container."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional
from urllib.request import Request, urlopen

from ..models import SendingAccount, SendJob, SentEmail, SequenceState, SuppressionEntry, TrackingEvent


class D1SendStore:
    """Mirror ``SendStore`` through the Worker-only sender storage bridge."""

    def __init__(self, bridge_url: str | None = None) -> None:
        self._url = (bridge_url or os.getenv("AUTOREACH_D1_BRIDGE_URL", "http://autoreach.storage")).rstrip("/")

    def insert_sent(self, sent: SentEmail) -> None:
        self._call("upsert-sent", {"sent": sent.model_dump(mode="json")})

    def update_sent_status(self, sent_id: str, status: str, **flags: bool) -> None:
        self._call("update-sent", {"id": sent_id, "status": status, "flags": flags})

    def get_sent(self, sent_id: str) -> Optional[dict]:
        return self._call("get-sent", {"id": sent_id}).get("sent")

    def get_sent_by_message_id(self, message_id: str) -> Optional[dict]:
        return self._call("get-by-message-id", {"message_id": message_id}).get("sent")

    def get_sent_by_idempotency_key(self, idempotency_key: str) -> Optional[dict]:
        return self._call("get-by-idempotency", {"idempotency_key": idempotency_key}).get("sent")

    def latest_sent_for_lead(self, lead_id: str) -> Optional[dict]:
        return self._call("latest-sent", {"lead_id": lead_id}).get("sent")

    def list_sent(self, status: Optional[str] = None) -> list[dict]:
        return list(self._call("list-sent", {"status": status}).get("items", []))

    def insert_event(self, event: TrackingEvent) -> None:
        self._call("insert-event", {"event": event.model_dump(mode="json")})

    def get_events(self, sent_email_id: str) -> list[dict]:
        return list(self._call("get-events", {"sent_email_id": sent_email_id}).get("items", []))

    def count_events_by_type(self) -> dict[str, int]:
        return dict(self._call("event-counts", {}).get("counts", {}))

    def upsert_sequence(self, state: SequenceState) -> None:
        state.updated_at = datetime.utcnow()
        self._call("upsert-sequence", {"sequence": state.model_dump(mode="json")})

    def schedule_followup(self, state: SequenceState) -> dict[str, Any]:
        return self._call("schedule-followup", {"sequence": state.model_dump(mode="json")})

    def get_sequence(self, lead_id: str) -> Optional[SequenceState]:
        value = self._call("get-sequence", {"lead_id": lead_id}).get("sequence")
        return SequenceState.model_validate(value) if value else None

    def list_active_sequences(self) -> list[SequenceState]:
        values = self._call("active-sequences", {}).get("items", [])
        return [SequenceState.model_validate(value) for value in values]

    def upsert_account(self, account: SendingAccount) -> None:
        self._call("upsert-account", {"account": account.model_dump(mode="json")})

    def list_accounts(self) -> list[SendingAccount]:
        return [SendingAccount.model_validate(value) for value in self._call("list-accounts", {}).get("items", [])]

    def reserve_capacity(
        self,
        *,
        idempotency_key: str,
        account_email: str,
        recipient: str,
        reserved_at: datetime,
        daily_limit: int,
        hourly_limit: int,
        burst_per_minute: int,
        domain_spacing_seconds: int,
    ) -> bool:
        domain = recipient.rsplit("@", 1)[-1].lower()
        value = self._call("reserve-capacity", {
            "idempotency_key": idempotency_key,
            "account_email": account_email,
            "recipient_domain": domain,
            "reserved_at": reserved_at.isoformat(),
            "daily_limit": daily_limit,
            "hourly_limit": hourly_limit,
            "burst_per_minute": burst_per_minute,
            "domain_spacing_seconds": domain_spacing_seconds,
        })
        return bool(value.get("reserved"))

    def add_suppression(self, entry: SuppressionEntry) -> None:
        self._call("upsert-suppression", {"entry": entry.model_dump(mode="json")})

    def is_suppressed(self, email: str) -> bool:
        return bool(self._call("is-suppressed", {"email": email}).get("suppressed"))

    def list_suppressions(self) -> list[dict]:
        return list(self._call("list-suppressions", {}).get("items", []))

    def upsert_job(self, job: SendJob) -> None:
        self._call("upsert-job", {"job": job.model_dump(mode="json")})

    def metrics(self, account_email: Optional[str] = None) -> dict[str, int]:
        return dict(self._call("metrics", {"account_email": account_email}).get("metrics", {}))

    def close(self) -> None:
        return None

    def _call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self._url}/v1/sender/{operation}",
            data=json.dumps(payload, default=str).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=15) as response:  # nosec B310 Worker virtual host
            value = json.loads(response.read().decode())
        if not isinstance(value, dict):
            raise RuntimeError("D1 sender bridge returned an invalid response")
        return value
