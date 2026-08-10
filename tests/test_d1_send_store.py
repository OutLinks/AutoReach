from __future__ import annotations

import importlib
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import urlparse

from orchestrator.adapters.live import _load_agent

_load_agent("agent4-sender", "agent4_sender")

D1SendStore = importlib.import_module("agent4_sender.storage.d1_send_store").D1SendStore
SentEmail = importlib.import_module("agent4_sender.models").SentEmail
SequenceState = importlib.import_module("agent4_sender.models").SequenceState


class _Response:
    def __init__(self, value: dict) -> None:
        self.value = value

    def read(self) -> bytes:
        return json.dumps(self.value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request, timeout: int):
        operation = urlparse(request.full_url).path.rsplit("/", 1)[-1]
        payload = json.loads(request.data.decode())
        self.calls.append((operation, payload))
        if operation == "get-by-idempotency":
            return _Response({"sent": None})
        if operation == "get-sequence":
            return _Response({"sequence": None})
        if operation == "active-sequences":
            return _Response({"items": []})
        if operation == "metrics":
            return _Response({"metrics": {"sent": 0}})
        if operation == "reserve-capacity":
            return _Response({"reserved": True})
        if operation == "schedule-followup":
            return _Response({"job_id": "followup-lead-1-day3", "workflow_instance_id": "wf-1"})
        return _Response({"saved": True})


class D1SendStoreTests(unittest.TestCase):
    def test_sender_records_and_sequences_use_named_bridge_operations(self) -> None:
        bridge = _Bridge()
        with patch("agent4_sender.storage.d1_send_store.urlopen", side_effect=bridge):
            store = D1SendStore("http://autoreach.storage")
            store.insert_sent(SentEmail(email_id="email-1", lead_id="lead-1", idempotency_key="key-1"))
            self.assertIsNone(store.get_sent_by_idempotency_key("key-1"))
            sequence = SequenceState(lead_id="lead-1", email_id="email-1")
            store.upsert_sequence(sequence)
            workflow = store.schedule_followup(sequence)
            self.assertEqual(workflow["workflow_instance_id"], "wf-1")
            self.assertTrue(store.reserve_capacity(
                idempotency_key="key-1", account_email="sender@example.com",
                recipient="lead@acme.example", reserved_at=datetime.now(timezone.utc),
                daily_limit=50, hourly_limit=10, burst_per_minute=3,
                domain_spacing_seconds=30,
            ))
            self.assertEqual(store.metrics(), {"sent": 0})

        self.assertEqual([name for name, _ in bridge.calls], [
            "upsert-sent", "get-by-idempotency", "upsert-sequence",
            "schedule-followup", "reserve-capacity", "metrics",
        ])
        self.assertEqual(bridge.calls[0][1]["sent"]["idempotency_key"], "key-1")
        self.assertEqual(bridge.calls[2][1]["sequence"]["lead_id"], "lead-1")
        self.assertEqual(bridge.calls[4][1]["recipient_domain"], "acme.example")


if __name__ == "__main__":
    unittest.main()
