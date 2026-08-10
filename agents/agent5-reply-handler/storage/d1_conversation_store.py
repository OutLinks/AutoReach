"""D1-backed Agent 5 conversation memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from core.worker_bridge import WorkerBridgeClient

from ..models import Conversation, ConversationMessage, HandoffPackage, Notification, ReplyJob


class D1ConversationStore:
    durable = True

    def __init__(self, bridge_url: str | None = None) -> None:
        self._bridge = WorkerBridgeClient(bridge_url)

    def get_conversation(self, lead_id: str) -> Optional[Conversation]:
        value = self._call("get-conversation", {"lead_id": lead_id}).get("conversation")
        return Conversation.model_validate(value) if value else None

    def upsert_conversation(self, conversation: Conversation) -> None:
        conversation.updated_at = datetime.utcnow()
        self._call("upsert-conversation", {"conversation": conversation.model_dump(mode="json")})

    def count_exchanges(self, lead_id: str) -> int:
        return int(self._call("count-exchanges", {"lead_id": lead_id}).get("count", 0))

    def add_message(self, message: ConversationMessage) -> bool:
        return bool(self._call("add-message", {"message": message.model_dump(mode="json")}).get("saved"))

    def get_messages(self, lead_id: str) -> list[dict]:
        return list(self._call("get-messages", {"lead_id": lead_id}).get("items", []))

    def add_handoff(self, handoff: HandoffPackage) -> None:
        self._call("add-handoff", {
            "id": f"{handoff.lead_id}-{int(handoff.created_at.timestamp())}",
            "handoff": handoff.model_dump(mode="json"),
        })

    def add_notification(self, notification: Notification) -> None:
        self._call("add-notification", {"notification": notification.model_dump(mode="json")})

    def list_notifications(self) -> list[dict]:
        return list(self._call("list-notifications", {}).get("items", []))

    def upsert_job(self, job: ReplyJob) -> None:
        self._call("upsert-job", {"job": job.model_dump(mode="json")})

    def stop_sequence(self, lead_id: str, status: str) -> None:
        self._call("stop-sequence", {"lead_id": lead_id, "status": status})

    def claim_inbound(self, reply_id: str, provider_event_id: str, payload: dict) -> str | None:
        lease = str(uuid4())
        value = self._call("claim-inbound", {
            "id": reply_id,
            "provider_event_id": provider_event_id or None,
            "payload": payload,
            "lease_token": lease,
            "lease_expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        })
        return lease if value.get("claimed") else None

    def finish_inbound(self, reply_id: str, lease: str, error: str = "") -> None:
        operation = "fail-inbound" if error else "complete-inbound"
        self._call(operation, {"id": reply_id, "lease_token": lease, "error": error})

    def reserve_outbound(self, message: ConversationMessage) -> bool:
        return bool(self._call(
            "reserve-outbound", {"message": message.model_dump(mode="json")}
        ).get("reserved"))

    def finish_outbound(
        self, message_id: str, provider_message_id: str, action_taken: str, success: bool
    ) -> None:
        self._call("finish-outbound", {
            "id": message_id,
            "message_id": provider_message_id,
            "action_taken": action_taken,
            "success": success,
        })

    def close(self) -> None:
        return None

    def _call(self, operation: str, payload: dict) -> dict:
        return self._bridge.call("replies", operation, payload)
