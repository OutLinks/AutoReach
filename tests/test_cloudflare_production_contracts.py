from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api.d1_config_store import D1ConfigStore
from api.executor import JobExecutor
from api.jobs import JobRecord
from orchestrator.adapters.live import LiveAdapter, _load_agent
from orchestrator.config import OrchestratorConfig
from orchestrator.models import PipelineLead
from orchestrator.state_machine import FIND


class CloudflareProductionContractTests(unittest.TestCase):
    def test_d1_config_rejects_plaintext_secrets_and_applies_only_nonsecrets(self) -> None:
        bridge = MagicMock()
        bridge.call.return_value = {
            "configured": True,
            "values": {"simulate": "false", "scheduler_timezone": "Asia/Colombo"},
        }
        with patch("api.d1_config_store.WorkerBridgeClient", return_value=bridge):
            store = D1ConfigStore()
            with self.assertRaisesRegex(ValueError, "Worker Secrets"):
                store.update({"smtp_password": "plaintext"})
            with patch.dict("os.environ", {}, clear=True):
                store.apply_to_process()
                import os
                self.assertEqual(os.environ["AUTOREACH_SIMULATE"], "false")
                self.assertNotIn("SMTP_PASSWORD", os.environ)

    def test_orchestrator_selects_d1_without_opening_sqlite(self) -> None:
        config = OrchestratorConfig(storage_backend="d1")
        durable = MagicMock()
        with patch("orchestrator.orchestrator.D1OrchestratorStore", return_value=durable), patch(
            "orchestrator.orchestrator.OrchestratorStore"
        ) as sqlite_store:
            from orchestrator.orchestrator import Orchestrator
            orchestrator = Orchestrator(config)
        self.assertIs(orchestrator.store, durable)
        sqlite_store.assert_not_called()

    def test_qualified_agent1_source_is_promoted_to_discovered(self) -> None:
        existing = PipelineLead(id="lead-1", state="qualified")
        store = SimpleNamespace(
            all_leads=lambda: [existing],
            saved=[],
            upserted=[],
        )
        store.save_artifact = lambda **value: store.saved.append(value)
        store.upsert_lead = lambda lead: store.upserted.append(lead)
        adapter = LiveAdapter(FIND)
        added = asyncio.run(adapter._ingest_new_leads(
            store,
            datetime.now(timezone.utc),
            records=[{
                "id": "lead-1", "email": "lead@example.com",
                "company_name": "Acme", "lead_score": 92,
            }],
        ))
        self.assertEqual(added, 1)
        self.assertEqual(store.upserted[0].state, "discovered")
        self.assertEqual(store.upserted[0].quality_score, 0.92)

    def test_production_container_declares_all_durable_backends(self) -> None:
        source = open("src/index.ts", encoding="utf-8").read()
        for value in (
            "AUTOREACH_STORAGE_BACKEND: \"d1\"",
            "AUTOREACH_LEAD_PIPELINE_BACKEND: \"d1\"",
            "AUTOREACH_ARTIFACT_BACKEND: \"r2\"",
            "AUTOREACH_EMAIL_WRITER_STORAGE_BACKEND: \"d1\"",
            "AUTOREACH_SENDER_STORAGE_BACKEND: \"d1\"",
            "AUTOREACH_REPLY_STORAGE_BACKEND: \"d1\"",
        ):
            self.assertIn(value, source)

    def test_durable_tick_converts_utc_to_configured_timezone(self) -> None:
        seen = []

        async def tick(value):
            seen.append(value)
            return {}

        orchestrator = SimpleNamespace(tick=tick)
        executor = JobExecutor(MagicMock(), orchestrator, MagicMock())
        job = JobRecord(
            id="tick-1",
            kind="pipeline.tick",
            payload={"now": "2026-08-10T00:00:00+00:00", "timezone": "Asia/Colombo"},
            created_at=datetime.now(timezone.utc),
        )
        asyncio.run(executor._execute(job))
        self.assertEqual(seen[0].utcoffset().total_seconds(), 5.5 * 60 * 60)
        self.assertEqual(seen[0].hour, 5)
        self.assertEqual(seen[0].minute, 30)

    def test_agent5_reply_message_id_is_retry_stable(self) -> None:
        module = _load_agent("agent5-reply-handler", "agent5_reply_handler")
        sender_module = __import__(
            "agent5_reply_handler.layers.output.reply_sender", fromlist=["ReplySender"]
        )
        config = module.ServiceConfig(simulate=True)
        sender = sender_module.ReplySender(config)

        async def send_once():
            return await sender.send(
                to_email="lead@example.com",
                subject="Re: Hello",
                body="Thanks",
                idempotency_key="provider-event-123",
            )

        first = asyncio.run(send_once())
        second = asyncio.run(send_once())
        self.assertTrue(first[0])
        self.assertEqual(first[1], second[1])


if __name__ == "__main__":
    unittest.main()
