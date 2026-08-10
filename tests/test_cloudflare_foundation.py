from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CloudflareFoundationTests(unittest.TestCase):
    def test_worker_has_one_stable_container_and_durable_bindings(self) -> None:
        config = json.loads((ROOT / "wrangler.jsonc").read_text())
        self.assertEqual(config["main"], "src/index.ts")
        self.assertEqual(config["containers"][0]["class_name"], "AutoReachContainer")
        self.assertEqual(config["containers"][0]["max_instances"], 1)
        self.assertEqual(config["containers"][0]["image"], "./Dockerfile")
        self.assertEqual(
            config["migrations"][0]["new_sqlite_classes"], ["AutoReachContainer"]
        )
        self.assertEqual(config["d1_databases"][0]["binding"], "AUTOREACH_DB")
        self.assertEqual(config["r2_buckets"][0]["binding"], "AUTOREACH_ARTIFACTS")
        self.assertEqual(config["queues"]["consumers"][0]["max_concurrency"], 1)

    def test_docker_entrypoint_keeps_cloudflare_port_contract(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        start_script = (ROOT / "scripts" / "start-api.sh").read_text()
        self.assertIn("PORT=8000", dockerfile)
        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertIn('"${PORT:-8000}"', start_script)

    def test_container_selects_durable_agent_storage_adapters(self) -> None:
        worker = (ROOT / "src" / "index.ts").read_text()
        self.assertIn('AUTOREACH_LEAD_PIPELINE_BACKEND: "d1"', worker)
        self.assertIn('AUTOREACH_ARTIFACT_BACKEND: "r2"', worker)
        self.assertIn('AUTOREACH_EMAIL_WRITER_STORAGE_BACKEND: "d1"', worker)
        self.assertIn('AUTOREACH_RESEARCH_READER_BACKEND: "d1"', worker)
        self.assertIn('AUTOREACH_SENDER_STORAGE_BACKEND: "d1"', worker)
        self.assertIn('handleSenderBridge', worker)
        self.assertIn('handleEmailWriterBridge', worker)
        self.assertIn('handleResearchIndexBridge', worker)


if __name__ == "__main__":
    unittest.main()
