from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from api.jobs import D1JobStore, JobStore


class _BridgeResponse:
    def __init__(self, payload: dict) -> None:
        import json

        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _D1BridgeFake:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.last_claim: dict | None = None

    def __call__(self, request, timeout: int = 0) -> _BridgeResponse:
        import json

        action = urlparse(request.full_url).path.rsplit("/", 1)[-1]
        payload = json.loads(request.data.decode("utf-8"))
        if action == "create":
            dedupe_key = payload.get("dedupe_key")
            existing = next(
                (
                    job
                    for job in self.jobs.values()
                    if dedupe_key and job.get("dedupe_key") == dedupe_key
                ),
                None,
            )
            if existing:
                return _BridgeResponse({"job": existing, "created": False})
            job = {
                "id": payload["id"],
                "kind": payload["kind"],
                "status": "queued",
                "payload": payload["payload"],
                "result": None,
                "error": "",
                "dedupe_key": dedupe_key,
                "created_at": payload["created_at"],
                "started_at": None,
                "completed_at": None,
            }
            self.jobs[job["id"]] = job
            return _BridgeResponse({"job": job, "created": True})
        if action == "get":
            return _BridgeResponse({"job": self.jobs[payload["id"]]})
        if action == "list":
            return _BridgeResponse({"jobs": list(self.jobs.values())})
        if action == "claim":
            self.last_claim = payload
            job = self.jobs[payload["id"]]
            if job["status"] != "queued":
                return _BridgeResponse({"job": None, "claimed": False})
            job["status"] = "running"
            job["started_at"] = payload["started_at"]
            return _BridgeResponse({"job": job, "claimed": True})
        if action == "succeed":
            job = self.jobs[payload["id"]]
            job.update(
                status="succeeded",
                result=payload["result"],
                completed_at=payload["completed_at"],
            )
            return _BridgeResponse({"job": job})
        if action == "fail":
            job = self.jobs[payload["id"]]
            job.update(
                status="failed",
                error=payload["error"],
                completed_at=payload["completed_at"],
            )
            return _BridgeResponse({"job": job})
        if action == "retry":
            job = self.jobs[payload["id"]]
            if job["status"] != "failed":
                return _BridgeResponse({"job": None, "requeued": False})
            job.update(status="queued", started_at=None, completed_at=None)
            return _BridgeResponse({"job": job, "requeued": True})
        if action == "recover":
            for job in self.jobs.values():
                if job["status"] in {"queued", "running"}:
                    job["status"] = "queued"
                    job["started_at"] = None
            return _BridgeResponse(
                {"job_ids": [job["id"] for job in self.jobs.values() if job["status"] == "queued"]}
            )
        raise AssertionError(f"Unexpected bridge action {action}")


class JobStoreTests(unittest.TestCase):
    def test_incomplete_job_is_recovered_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "jobs.db"
            store = JobStore(path)
            job, created = store.create("pipeline.find")
            self.assertTrue(created)
            store.mark_running(job.id)
            store.close()

            recovered_store = JobStore(path)
            self.assertEqual(recovered_store.recover_incomplete(), [job.id])
            recovered = recovered_store.get(job.id)
            self.assertEqual(recovered.status, "queued")
            self.assertIn("Recovered", recovered.error)
            recovered_store.close()

    def test_dedupe_key_returns_the_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = JobStore(Path(tempdir) / "jobs.db")
            first, created = store.create("pipeline.tick", dedupe_key="tick:one")
            self.assertTrue(created)
            second, created = store.create("pipeline.tick", dedupe_key="tick:one")
            self.assertFalse(created)
            self.assertEqual(first.id, second.id)
            store.close()

    def test_claim_for_execution_is_compare_and_set(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = JobStore(Path(tempdir) / "jobs.db")
            job, _ = store.create("pipeline.find")
            claimed = store.claim_for_execution(job.id)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.status, "running")
            self.assertIsNone(store.claim_for_execution(job.id))
            store.close()

    def test_d1_adapter_preserves_job_repository_contract(self) -> None:
        bridge = _D1BridgeFake()
        with patch("api.jobs.urlopen", side_effect=bridge):
            store = D1JobStore("http://autoreach.storage")
            job, created = store.create("pipeline.find", {"source": "test"}, "one")
            self.assertTrue(created)
            duplicate, created = store.create("pipeline.find", {}, "one")
            self.assertFalse(created)
            self.assertEqual(duplicate.id, job.id)
            self.assertEqual(store.claim_for_execution(job.id).status, "running")
            self.assertIsNotNone(bridge.last_claim)
            self.assertTrue(bridge.last_claim["lease_token"])
            self.assertTrue(bridge.last_claim["lease_expires_at"])
            self.assertTrue(bridge.last_claim["executor_id"])
            self.assertIsNone(store.claim_for_execution(job.id))
            store.mark_succeeded(job.id, {"lead_count": 1})
            completed = store.get(job.id)
            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(completed.result, {"lead_count": 1})
            self.assertEqual(store.recover_incomplete(), [])

            failed, _ = store.create("pipeline.find")
            store.claim_for_execution(failed.id)
            store.mark_failed(failed.id, "temporary failure")
            self.assertEqual(store.requeue_failed(failed.id).status, "queued")


if __name__ == "__main__":
    unittest.main()
