"""D1 repository for interactive API workflow records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.worker_bridge import WorkerBridgeClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class D1WorkflowStore:
    def __init__(self, bridge_url: str | None = None) -> None:
        self._bridge = WorkerBridgeClient(bridge_url)

    def create_research(self, *, lead_id: str = "", company: str, prompt: str = "",
                        summary: str = "", sections: list[dict[str, str]] | None = None,
                        sources: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return self._item("create-research", {
            "id": str(uuid4()), "lead_id": lead_id, "company": company, "prompt": prompt,
            "summary": summary, "sections": sections or [], "sources": sources or [], "now": _now(),
        })

    def list_research(self, lead_id: str | None = None) -> list[dict[str, Any]]:
        return self._items("list-research", {"lead_id": lead_id})

    def get_research(self, document_id: str) -> dict[str, Any]:
        return self._required("get-research", {"id": document_id}, document_id)

    def update_research(self, document_id: str, values: dict[str, Any]) -> dict[str, Any]:
        return self._required("update-research", {"id": document_id, "values": values, "now": _now()}, document_id)

    def create_draft(self, *, lead_id: str, campaign_id: str, subject: str, body: str,
                     tone: str = "", instructions: str = "", source_email_id: str = "") -> dict[str, Any]:
        return self._item("create-draft", {
            "id": str(uuid4()), "lead_id": lead_id, "campaign_id": campaign_id,
            "subject": subject, "body": body, "tone": tone, "instructions": instructions,
            "source_email_id": source_email_id, "now": _now(),
        })

    def list_drafts(self, *, lead_id: str | None = None, campaign_id: str | None = None,
                    status: str | None = None) -> list[dict[str, Any]]:
        return self._items("list-drafts", {"lead_id": lead_id, "campaign_id": campaign_id, "status": status})

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self._required("get-draft", {"id": draft_id}, draft_id)

    def get_draft_internal(self, draft_id: str) -> dict[str, Any]:
        return self._required("get-draft-internal", {"id": draft_id}, draft_id)

    def update_draft(self, draft_id: str, values: dict[str, Any]) -> dict[str, Any]:
        value = self._call("update-draft", {"id": draft_id, "values": values, "now": _now()})
        if value.get("error") == "sent":
            raise ValueError("Sent emails cannot be edited")
        return self._required_value(value, draft_id)

    def set_draft_content(self, draft_id: str, *, subject: str, body: str,
                          source_email_id: str = "") -> dict[str, Any]:
        return self._required("set-draft-content", {
            "id": draft_id, "subject": subject, "body": body,
            "source_email_id": source_email_id, "now": _now(),
        }, draft_id)

    def set_draft_status(self, draft_id: str, status: str) -> dict[str, Any]:
        return self._required("set-draft-status", {"id": draft_id, "status": status, "now": _now()}, draft_id)

    def create_search(self, query: str, filters: dict[str, Any], result_limit: int) -> dict[str, Any]:
        return self._item("create-search", {
            "id": str(uuid4()), "query": query, "filters": filters,
            "result_limit": result_limit, "now": _now(),
        })

    def finish_search(self, search_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        return self._required("finish-search", {"id": search_id, "results": results, "now": _now()}, search_id)

    def fail_search(self, search_id: str, error: str) -> None:
        self._call("fail-search", {"id": search_id, "error": error[:4000], "now": _now()})

    def list_searches(self) -> list[dict[str, Any]]:
        return self._items("list-searches", {})

    def get_search(self, search_id: str) -> dict[str, Any]:
        return self._required("get-search", {"id": search_id}, search_id)

    def search_results(self, search_id: str, lead_ids: list[str]) -> list[dict[str, Any]]:
        if not lead_ids:
            return []
        return self._items("search-results", {"id": search_id, "lead_ids": lead_ids})

    def mark_search_results_imported(self, search_id: str, lead_ids: list[str]) -> None:
        if lead_ids:
            self._call("mark-search-imported", {"id": search_id, "lead_ids": lead_ids})

    def add_mailbox_message(self, *, message_id: str, thread_id: str, lead_id: str,
                            direction: str, from_addr: str, to_addr: str, subject: str,
                            body: str, sent_at: str | None = None, bounced: bool = False) -> None:
        self._call("add-mailbox-message", {
            "message_id": message_id, "thread_id": thread_id, "lead_id": lead_id,
            "direction": direction, "from_addr": from_addr, "to_addr": to_addr,
            "subject": subject, "body": body, "sent_at": sent_at or _now(), "bounced": bounced,
        })

    def list_mailbox_threads(self, folder: str, page: int, page_size: int) -> dict[str, Any]:
        return self._call("list-mailbox-threads", {"folder": folder, "page": page, "page_size": page_size})

    def get_mailbox_thread(self, thread_id: str) -> dict[str, Any]:
        return self._required("get-mailbox-thread", {"id": thread_id}, thread_id)

    def mark_thread_read(self, thread_id: str) -> None:
        if not self._call("mark-thread-read", {"id": thread_id}).get("updated"):
            raise KeyError(thread_id)

    def create_conversation(self, message: str) -> str:
        conversation_id = str(uuid4())
        self._call("create-conversation", {
            "id": conversation_id, "message_id": str(uuid4()), "message": message, "now": _now(),
        })
        return conversation_id

    def add_operator_message(self, conversation_id: str, role: str, content: str,
                             tool_calls: list[dict[str, Any]] | None = None) -> None:
        self._call("add-operator-message", {
            "id": str(uuid4()), "conversation_id": conversation_id, "role": role,
            "content": content, "tool_calls": tool_calls, "now": _now(),
        })

    def add_timeline_step(self, conversation_id: str, *, step: str, agent: str, action: str,
                          status: str, started_at: str | None = None,
                          finished_at: str | None = None) -> int:
        return int(self._call("add-timeline-step", {
            "conversation_id": conversation_id, "step": step, "agent": agent,
            "action": action, "status": status, "started_at": started_at,
            "finished_at": finished_at,
        }).get("id", 0))

    def update_timeline_step(self, timeline_id: int, status: str) -> None:
        self._call("update-timeline-step", {"id": timeline_id, "status": status, "now": _now()})

    def list_conversations(self) -> list[dict[str, Any]]:
        return self._items("list-conversations", {})

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return self._required("get-conversation", {"id": conversation_id}, conversation_id)

    def conversation_timeline(self, conversation_id: str) -> list[dict[str, Any]]:
        value = self._call("conversation-timeline", {"id": conversation_id})
        if not value.get("found"):
            raise KeyError(conversation_id)
        return list(value.get("items", []))

    def close(self) -> None:
        return None

    def _call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._bridge.call("workflows", operation, payload)

    def _item(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._call(operation, payload).get("item") or {})

    def _items(self, operation: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._call(operation, payload).get("items", []))

    def _required(self, operation: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        return self._required_value(self._call(operation, payload), key)

    @staticmethod
    def _required_value(value: dict[str, Any], key: str) -> dict[str, Any]:
        item = value.get("item")
        if not item:
            raise KeyError(key)
        return dict(item)
