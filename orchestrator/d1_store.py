"""D1-backed coordination store for Cloudflare Container production."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.worker_bridge import WorkerBridgeClient

from .campaigns import CampaignBrief
from .models import PipelineLead, RunRecord


class D1OrchestratorStore:
    def __init__(self, bridge_url: str | None = None) -> None:
        self._bridge = WorkerBridgeClient(bridge_url)

    def upsert_lead(self, lead: PipelineLead) -> None:
        lead.updated_at = datetime.utcnow()
        self._call("upsert-lead", {"lead": lead.model_dump(mode="json")})

    def get_lead(self, lead_id: str) -> Optional[PipelineLead]:
        value = self._call("get-lead", {"id": lead_id}).get("lead")
        return PipelineLead.model_validate(value) if value else None

    def leads_in_state(self, state: str) -> list[PipelineLead]:
        return self._leads(self._call("list-leads", {"state": state}).get("items", []))

    def count_by_state(self) -> dict[str, int]:
        return dict(self._call("lead-counts", {}).get("counts", {}))

    def all_leads(self) -> list[PipelineLead]:
        return self._leads(self._call("list-leads", {}).get("items", []))

    def log_event(self, lead_id: str, from_state: str, to_state: str, note: str = "") -> None:
        self._call("log-event", {
            "lead_id": lead_id, "from_state": from_state, "to_state": to_state, "note": note,
        })

    def count_transitions_to(self, state: str) -> int:
        return int(self._call("event-count", {"to_state": state}).get("count", 0))

    def count_events_with_note(self, note: str) -> int:
        return int(self._call("event-count", {"note": note}).get("count", 0))

    def record_run(self, run: RunRecord) -> None:
        self._call("record-run", {"run": run.model_dump(mode="json")})

    def recent_runs(self, stage: str, limit: int = 10) -> list[dict]:
        return list(self._call("recent-runs", {"stage": stage, "limit": limit}).get("items", []))

    def dead_letter(self, lead_id: str, stage: str, reason: str) -> None:
        self._call("dead-letter", {"lead_id": lead_id, "stage": stage, "reason": reason})

    def dead_letter_count(self) -> int:
        return int(self._call("dead-letter-count", {}).get("count", 0))

    def list_dead_letter(self) -> list[dict]:
        return list(self._call("list-dead-letter", {}).get("items", []))

    def save_artifact(
        self,
        artifact_id: str,
        kind: str,
        payload: dict,
        lead_id: str = "",
        source_job: str = "",
    ) -> None:
        self._call("save-artifact", {
            "id": artifact_id, "kind": kind, "payload": payload,
            "lead_id": lead_id, "source_job": source_job,
        })

    def save_campaign(self, brief: CampaignBrief) -> None:
        brief.updated_at = datetime.utcnow()
        self._call("save-campaign", {"campaign": brief.model_dump(mode="json")})

    def get_campaign(self, campaign_id: str) -> Optional[CampaignBrief]:
        value = self._call("get-campaign", {"id": campaign_id}).get("campaign")
        return CampaignBrief.model_validate(value) if value else None

    def active_campaign(self) -> Optional[CampaignBrief]:
        value = self._call("active-campaign", {}).get("campaign")
        return CampaignBrief.model_validate(value) if value else None

    def list_campaigns(self, limit: int = 100, offset: int = 0) -> list[CampaignBrief]:
        values = self._call("list-campaigns", {"limit": limit, "offset": offset}).get("items", [])
        return [CampaignBrief.model_validate(value) for value in values]

    def activate_campaign(self, campaign_id: str) -> CampaignBrief:
        value = self._call("activate-campaign", {"id": campaign_id}).get("campaign")
        if not value:
            raise ValueError(f"Campaign {campaign_id} does not exist")
        return CampaignBrief.model_validate(value)

    def close(self) -> None:
        return None

    def _call(self, operation: str, payload: dict) -> dict:
        return self._bridge.call("orchestrator", operation, payload)

    @staticmethod
    def _leads(values: list[dict]) -> list[PipelineLead]:
        return [PipelineLead.model_validate(value) for value in values]
