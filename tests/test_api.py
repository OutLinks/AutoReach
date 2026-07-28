from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import AppSettings
from orchestrator.adapters.live import _load_agent


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings = AppSettings(
            environment="test",
            data_dir=Path(self.tempdir.name),
            scheduler_enabled=False,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_api_only_root_and_unauthenticated_config(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            root = client.get("/")
            self.assertEqual(root.status_code, 200)
            self.assertEqual(root.headers["content-type"], "application/json")
            self.assertEqual(root.json()["authentication"], "disabled")
            self.assertEqual(client.get("/assets/app.js").status_code, 404)
            response = client.get("/v1/config")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["simulate"])
            schema = client.get("/openapi.json").json()
            self.assertNotIn("securitySchemes", schema.get("components", {}))

    def test_simulated_find_runs_as_durable_job(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/v1/jobs/find")
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]

            job = None
            for _ in range(100):
                job_response = client.get(f"/v1/jobs/{job_id}")
                self.assertEqual(job_response.status_code, 200)
                job = job_response.json()
                if job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)

            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "succeeded", job.get("error"))
            leads = client.get("/v1/leads")
            self.assertEqual(leads.status_code, 200)
            self.assertGreater(leads.json()["total"], 0)

    def test_unknown_stage_is_rejected(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/v1/jobs/stages/not-a-stage")
            self.assertEqual(response.status_code, 404)

    def test_settings_are_database_backed_and_secrets_are_masked(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.patch(
                "/v1/settings",
                json={
                    "values": {
                        "llm_provider": "openai",
                        "openai_api_key": "database-secret",
                        "leads_per_day": 75,
                    }
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            settings = {item["key"]: item for item in response.json()["items"]}
            self.assertEqual(settings["llm_provider"]["value"], "openai")
            self.assertEqual(settings["leads_per_day"]["value"], 75)
            self.assertEqual(settings["openai_api_key"]["value"], "")
            self.assertTrue(settings["openai_api_key"]["configured"])

    def test_sender_profile_is_database_backed_and_available_to_live_writer(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with TestClient(create_app(self.settings)) as client:
                response = client.patch(
                    "/v1/settings",
                    json={
                        "values": {
                            "sender_email": "januda@example.com",
                            "sender_first_name": "Januda",
                            "sender_last_name": "Lelwala",
                            "sender_title": "Founder",
                            "sender_company": "AutoReach",
                            "sender_signature": "Januda\nFounder, AutoReach",
                            "sender_linkedin_url": "https://linkedin.com/in/januda",
                            "sender_phone": "+94 00 000 0000",
                        }
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                settings = {item["key"]: item for item in response.json()["items"]}
                self.assertEqual(settings["sender_first_name"]["value"], "Januda")
                self.assertEqual(settings["sender_last_name"]["value"], "Lelwala")
                self.assertEqual(os.environ["SENDER_FIRST_NAME"], "Januda")
                self.assertEqual(os.environ["SENDER_LAST_NAME"], "Lelwala")

                _load_agent("agent3-email-writer", "agent3_email_writer")
                sender_profile = importlib.import_module(
                    "agent3_email_writer.layers.input.sender_profile"
                )
                profile = sender_profile.SenderProfileLoader().load()
                self.assertEqual(profile.full_name, "Januda Lelwala")
                self.assertEqual(profile.company, "AutoReach")
                self.assertEqual(profile.signature, "Januda\nFounder, AutoReach")

    def test_redis_url_is_database_backed_and_available_to_live_lead_finder(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with TestClient(create_app(self.settings)) as client:
                response = client.patch(
                    "/v1/settings",
                    json={"values": {"redis_url": "redis://redis:6379/0"}},
                )

                self.assertEqual(response.status_code, 200, response.text)
                settings = {item["key"]: item for item in response.json()["items"]}
                self.assertEqual(
                    settings["redis_url"]["value"], "redis://redis:6379/0"
                )
                self.assertEqual(os.environ["REDIS_URL"], "redis://redis:6379/0")

                lead_finder = _load_agent(
                    "agent1-lead-finder", "agent1_lead_finder"
                )
                self.assertEqual(
                    lead_finder.ServiceConfig().redis_url,
                    "redis://redis:6379/0",
                )

    def test_lead_discovery_providers_are_database_backed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with TestClient(create_app(self.settings)) as client:
                response = client.patch(
                    "/v1/settings",
                    json={
                        "values": {
                            "tavily_api_key": "database-tavily-key",
                            "tavily_enabled": True,
                            "lead_finder_source_urls": (
                                "https://example.com/directory,"
                                "https://example.org/companies"
                            ),
                        }
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                settings = {item["key"]: item for item in response.json()["items"]}
                self.assertTrue(settings["tavily_api_key"]["configured"])
                self.assertEqual(settings["tavily_api_key"]["value"], "")
                self.assertTrue(settings["tavily_enabled"]["value"])

                lead_finder = _load_agent(
                    "agent1-lead-finder", "agent1_lead_finder"
                )
                config = lead_finder.ServiceConfig()
                self.assertEqual(config.enabled_search_apis(), ["tavily"])
                self.assertEqual(
                    config.web_scraper_seed_urls,
                    [
                        "https://example.com/directory",
                        "https://example.org/companies",
                    ],
                )

    def test_agents_and_orchestrator_have_explicit_control_endpoints(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            agents = client.get("/v1/agents")
            self.assertEqual(agents.status_code, 200)
            names = {item["name"] for item in agents.json()["items"]}
            self.assertEqual(
                names,
                {
                    "agent1-lead-finder",
                    "agent2-research-analyst",
                    "agent3-email-writer",
                    "agent4-sender",
                    "agent5-reply-handler",
                },
            )

            agent_job = client.post(
                "/v1/agents/agent1-lead-finder/run",
                json={},
            )
            self.assertEqual(agent_job.status_code, 202)
            self.assertEqual(agent_job.json()["payload"]["stage"], "find")

            ambiguous_sender = client.post(
                "/v1/agents/agent4-sender/run",
                json={},
            )
            self.assertEqual(ambiguous_sender.status_code, 422)

            sender_job = client.post(
                "/v1/agents/agent4-sender/run",
                json={"stage": "send"},
            )
            self.assertEqual(sender_job.status_code, 202)
            self.assertEqual(sender_job.json()["payload"]["stage"], "send")

            orchestrator_job = client.post("/v1/orchestrator/cycle")
            self.assertEqual(orchestrator_job.status_code, 202)


class FirstRunSetupTests(unittest.TestCase):
    def test_user_selects_the_database_during_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            data_dir = Path(tempdir) / "bootstrap"
            selected = Path(tempdir) / "chosen" / "workspace.sqlite"
            settings = AppSettings(environment="test", data_dir=data_dir)

            with TestClient(create_app(settings)) as client:
                setup_status = client.get("/v1/setup").json()
                self.assertFalse(setup_status["configured"])
                self.assertEqual(client.get("/v1/config").status_code, 200)

                response = client.post(
                    "/v1/setup",
                    json={
                        "database_path": str(selected),
                        "settings": {"scheduler_timezone": "Asia/Colombo"},
                    },
                )
                self.assertEqual(response.status_code, 201, response.text)
                config = client.get("/v1/config")
                self.assertEqual(config.status_code, 200)
                self.assertEqual(config.json()["scheduler_timezone"], "Asia/Colombo")

            self.assertTrue(selected.exists())
            with sqlite3.connect(selected) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertTrue(
                {"app_meta", "app_settings", "api_jobs", "leads", "campaigns"}
                <= tables
            )


class SettingsTests(unittest.TestCase):
    def test_scheduler_interval_must_be_at_least_five_seconds(self) -> None:
        settings = AppSettings(environment="production", scheduler_interval_seconds=4)
        with self.assertRaisesRegex(ValueError, "at least 5"):
            settings.validate()


if __name__ == "__main__":
    unittest.main()
