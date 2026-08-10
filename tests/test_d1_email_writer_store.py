from __future__ import annotations

import importlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from orchestrator.adapters.live import _load_agent

_load_agent("agent3-email-writer", "agent3_email_writer")
_load_agent("agent4-sender", "agent4_sender")
_load_agent("agent2-research-analyst", "agent2_research_analyst")
_load_agent("agent1-lead-finder", "agent1_lead_finder")

D1EmailDatabase = importlib.import_module(
    "agent3_email_writer.layers.output.d1_email_db"
).D1EmailDatabase
WrittenEmail = importlib.import_module("agent3_email_writer.models").WrittenEmail
EmailReader = importlib.import_module("agent4_sender.storage.email_reader").EmailReader
LeadReader = importlib.import_module("agent2_research_analyst.storage.lead_reader").LeadReader
ResearchReader = importlib.import_module(
    "agent3_email_writer.layers.input.research_reader"
).ResearchReader
DBWriter = importlib.import_module("agent1_lead_finder.storage.db_writer").DBWriter
Lead = importlib.import_module("agent1_lead_finder.models").Lead


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
        payload = json.loads(request.data.decode()) if request.data else {}
        self.calls.append((operation, payload))
        if request.get_method() == "GET":
            return _Response({"id": "research-1", "lead_id": "lead-1"})
        if operation == "get-email":
            return _Response({"email": None})
        if operation == "list-sendable":
            return _Response({"items": [{"id": "email-1", "recipient": "lead@example.com"}]})
        if operation == "list-leads":
            return _Response({"leads": [{"id": "lead-1", "lead_grade": "A"}]})
        if operation == "list-profiles":
            return _Response({"items": [{"lead_id": "lead-1", "artifact_key": "research/job/lead-1.json"}]})
        if operation == "persist-final-leads":
            return _Response({"persisted": len(payload["leads"])})
        return _Response({"saved": True})


class D1EmailWriterStoreTests(unittest.TestCase):
    def test_writer_and_sender_use_the_named_d1_email_operations(self) -> None:
        bridge = _Bridge()
        with patch("agent3_email_writer.layers.output.d1_email_db.urlopen", side_effect=bridge), patch(
            "agent4_sender.storage.email_reader.urlopen", side_effect=bridge
        ):
            store = D1EmailDatabase("http://autoreach.storage")
            store.insert_email(WrittenEmail(
                id="email-1", lead_id="lead-1", research_profile_id="research-1",
                subject="Subject", body="Body", recipient="lead@example.com", timezone="Europe/London",
            ))
            self.assertIsNone(store.get_email("missing"))
            emails = EmailReader("unused.db", backend="d1", bridge_url="http://autoreach.storage").read_sendable()

        self.assertEqual(emails[0]["recipient"], "lead@example.com")
        self.assertEqual([name for name, _ in bridge.calls], [
            "upsert-email", "get-email", "list-sendable",
        ])
        persisted = bridge.calls[0][1]["email"]
        self.assertEqual(persisted["timezone"], "Europe/London")
        self.assertEqual(bridge.calls[2][1]["statuses"], ["approved"])

    def test_research_reader_uses_durable_scored_leads(self) -> None:
        bridge = _Bridge()
        with patch("agent2_research_analyst.storage.lead_reader.urlopen", side_effect=bridge):
            reader = LeadReader(backend="d1", bridge_url="http://autoreach.storage")
            self.assertEqual(reader.read_by_id("lead-1"), {"id": "lead-1", "lead_grade": "A"})
            self.assertEqual(reader.read_all(), [{"id": "lead-1", "lead_grade": "A"}])

        self.assertEqual([name for name, _ in bridge.calls], ["list-leads", "list-leads"])
        self.assertEqual(bridge.calls[0][1]["lead_ids"], ["lead-1"])

    def test_email_writer_reads_indexed_r2_profiles_and_d1_leads(self) -> None:
        bridge = _Bridge()
        with patch("agent3_email_writer.layers.input.research_reader.urlopen", side_effect=bridge):
            pairs = ResearchReader(backend="d1", bridge_url="http://autoreach.storage").read_all()

        self.assertEqual(pairs, [
            ({"id": "research-1", "lead_id": "lead-1"}, {"id": "lead-1", "lead_grade": "A"}),
        ])
        self.assertEqual([name for name, _ in bridge.calls], [
            "list-profiles", "lead-1.json", "list-leads",
        ])

    def test_final_agent1_leads_are_written_to_canonical_d1_records(self) -> None:
        bridge = _Bridge()
        with patch("agent1_lead_finder.storage.db_writer.urlopen", side_effect=bridge):
            import asyncio
            written = asyncio.run(DBWriter(write_files=False).write([
                Lead(id="lead-1", email="lead@example.com", lead_score=92, lead_grade="A"),
                Lead(id="duplicate", is_duplicate=True),
            ], "job-1"))

        self.assertEqual(written, 1)
        self.assertEqual(bridge.calls[0][0], "persist-final-leads")
        self.assertEqual(bridge.calls[0][1]["leads"][0]["id"], "lead-1")


if __name__ == "__main__":
    unittest.main()
