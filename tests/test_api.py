from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import AppSettings


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.secret = "test-secret-with-more-than-24-characters"
        self.settings = AppSettings(
            environment="test",
            api_secret=self.secret,
            data_dir=Path(self.tempdir.name),
            scheduler_enabled=False,
        )
        self.headers = {"Authorization": f"Bearer {self.secret}"}

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_health_and_authentication(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/v1/config").status_code, 401)
            response = client.get("/v1/config", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["simulate"])

    def test_simulated_find_runs_as_durable_job(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/v1/jobs/find", headers=self.headers)
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]

            job = None
            for _ in range(100):
                job_response = client.get(f"/v1/jobs/{job_id}", headers=self.headers)
                self.assertEqual(job_response.status_code, 200)
                job = job_response.json()
                if job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)

            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "succeeded", job.get("error"))
            leads = client.get("/v1/leads", headers=self.headers)
            self.assertEqual(leads.status_code, 200)
            self.assertGreater(leads.json()["total"], 0)

    def test_unknown_stage_is_rejected(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/v1/jobs/stages/not-a-stage", headers=self.headers)
            self.assertEqual(response.status_code, 404)


class SettingsTests(unittest.TestCase):
    def test_production_requires_a_strong_secret(self) -> None:
        settings = AppSettings(environment="production", api_secret="short")
        with self.assertRaisesRegex(ValueError, "at least 24"):
            settings.validate()


if __name__ == "__main__":
    unittest.main()
