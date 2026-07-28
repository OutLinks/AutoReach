from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import AppSettings
from orchestrator.campaigns import (
    AgentInstructions,
    CampaignBrief,
    CampaignMessaging,
)
from orchestrator.models import DISCOVERED, NEW, PipelineLead


class InteractiveWorkflowApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings = AppSettings(
            environment="test",
            data_dir=Path(self.tempdir.name),
            scheduler_enabled=False,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _wait(client: TestClient, job_id: str) -> dict:
        for _ in range(200):
            job = client.get(f"/v1/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.01)
        raise AssertionError(f"Job {job_id} did not finish")

    @staticmethod
    def _seed(client: TestClient) -> tuple[PipelineLead, CampaignBrief]:
        lead = PipelineLead(
            id="lead-interactive-1",
            state=DISCOVERED,
            email="recipient@example.com",
            company="Example AI",
            industry="Software",
        )
        campaign = CampaignBrief(
            id="campaign-interactive-1",
            name="Interactive campaign",
            user_prompt="Offer practical AI engineering support.",
            messaging=CampaignMessaging(
                value_proposition="We help engineering teams ship reliable AI systems."
            ),
            agent_instructions=AgentInstructions(
                lead_finder="Find suitable software teams.",
                research_analyst="Find evidence-backed engineering priorities.",
                email_writer="Write a concise, truthful email.",
                sender="Respect all sender safeguards.",
                reply_handler="Answer only the question asked.",
            ),
        )
        client.app.state.orchestrator.store.upsert_lead(lead)
        client.app.state.orchestrator.store.save_campaign(campaign)
        return lead, campaign

    def test_research_is_editable_and_iterative(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            invalid = client.post("/v1/research", json={})
            self.assertEqual(invalid.status_code, 422)
            self.assertIsInstance(invalid.json()["detail"], list)

            lead, _ = self._seed(client)
            response = client.post(
                "/v1/research",
                json={
                    "lead_id": lead.id,
                    "prompt": "Focus on AI engineering hiring signals.",
                },
            )
            self.assertEqual(response.status_code, 202, response.text)
            job = self._wait(client, response.json()["job_id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
            research_id = job["result"]["research_id"]

            document = client.get(f"/v1/research/{research_id}")
            self.assertEqual(document.status_code, 200)
            self.assertEqual(document.json()["lead_id"], lead.id)
            self.assertEqual(document.json()["version"], 1)

            edited = client.patch(
                f"/v1/research/{research_id}",
                json={"summary": "User-edited research summary."},
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(edited.json()["version"], 2)

            iteration = client.post(
                f"/v1/research/{research_id}/iterate",
                json={"instructions": "Add a concise hiring-signals section."},
            )
            iteration_job = self._wait(client, iteration.json()["job_id"])
            self.assertEqual(iteration_job["status"], "succeeded", iteration_job["error"])
            self.assertEqual(iteration_job["result"]["document"]["version"], 3)
            self.assertEqual(
                client.get(f"/v1/research?lead_id={lead.id}").json()["items"][0]["id"],
                research_id,
            )
            restarted = client.patch(
                "/v1/settings",
                json={"values": {"leads_per_day": 51}},
            )
            self.assertEqual(restarted.status_code, 200, restarted.text)
            self.assertEqual(
                client.get(f"/v1/research/{research_id}").json()["version"],
                3,
            )

    def test_draft_approval_send_and_mailbox_reply_use_jobs(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            lead, campaign = self._seed(client)
            client.app.state.workflow_store.create_research(
                lead_id=lead.id,
                company=lead.company,
                summary="Example AI is hiring engineers for reliability work.",
                sections=[
                    {
                        "heading": "Hiring signal",
                        "body": "The user verified an engineering reliability role.",
                    }
                ],
            )
            generated = client.post(
                "/v1/emails/drafts",
                json={
                    "lead_id": lead.id,
                    "campaign_id": campaign.id,
                    "tone": "direct",
                    "instructions": "Mention engineering reliability.",
                },
            )
            job = self._wait(client, generated.json()["job_id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
            draft_id = job["result"]["draft_id"]
            self.assertIn(
                "hiring engineers for reliability work",
                job["result"]["draft"]["body"],
            )

            blocked = client.post(f"/v1/emails/drafts/{draft_id}/send")
            self.assertEqual(blocked.status_code, 409)
            regenerated = client.post(
                f"/v1/emails/drafts/{draft_id}/regenerate",
                json={"instructions": "Use a different opening."},
            )
            regenerated_job = self._wait(client, regenerated.json()["job_id"])
            self.assertEqual(
                regenerated_job["status"],
                "succeeded",
                regenerated_job["error"],
            )
            self.assertNotEqual(regenerated_job["result"]["draft_id"], draft_id)
            drafts = client.get(
                f"/v1/emails/drafts?lead_id={lead.id}&campaign_id={campaign.id}"
            ).json()["items"]
            self.assertEqual(len(drafts), 2)
            edited = client.patch(
                f"/v1/emails/drafts/{draft_id}",
                json={"subject": "A reliability idea for Example AI"},
            )
            self.assertEqual(edited.status_code, 200)
            approved = client.post(f"/v1/emails/drafts/{draft_id}/approve")
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["status"], "approved")

            sent_response = client.post(f"/v1/emails/drafts/{draft_id}/send")
            sent_job = self._wait(client, sent_response.json()["job_id"])
            self.assertEqual(sent_job["status"], "succeeded", sent_job["error"])
            self.assertTrue(sent_job["result"]["simulated"])
            sent_email_id = sent_job["result"]["sent_email_id"]

            sent_threads = client.get("/v1/mailbox/threads?folder=sent").json()
            self.assertEqual(sent_threads["total"], 1)
            thread_id = sent_threads["items"][0]["id"]
            self.assertEqual(thread_id, lead.id)

            event = client.post(
                "/v1/events/sender",
                json={
                    "event": "reply",
                    "sent_email_id": sent_email_id,
                    "detail": "Can you share more details?",
                },
            )
            event_job = self._wait(client, event.json()["id"])
            self.assertEqual(event_job["status"], "succeeded", event_job["error"])

            inbox = client.get("/v1/mailbox/threads?folder=inbox").json()
            self.assertEqual(inbox["total"], 1)
            self.assertTrue(inbox["items"][0]["unread"])
            thread = client.get(f"/v1/mailbox/threads/{thread_id}").json()
            self.assertEqual(thread["messages"][-1]["direction"], "in")
            marked = client.post(f"/v1/mailbox/threads/{thread_id}/mark-read")
            self.assertEqual(marked.status_code, 200)
            self.assertFalse(
                client.get("/v1/mailbox/threads?folder=inbox").json()["items"][0][
                    "unread"
                ]
            )

            reply = client.post(
                f"/v1/mailbox/threads/{thread_id}/reply",
                json={"body": "Absolutely — here are the details."},
            )
            reply_job = self._wait(client, reply.json()["job_id"])
            self.assertEqual(reply_job["status"], "succeeded", reply_job["error"])
            self.assertTrue(reply_job["result"]["simulated"])
            replied = client.get("/v1/mailbox/threads?folder=replied").json()
            self.assertEqual(replied["total"], 1)
            self.assertFalse(replied["items"][0]["unread"])

    def test_lead_search_previews_then_imports_selected_results(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.post(
                "/v1/lead-finding/searches",
                json={
                    "query": "AI engineering teams hiring platform engineers",
                    "filters": {
                        "industry": "Software",
                        "location": "Remote",
                        "size": "11-50",
                    },
                    "limit": 3,
                },
            )
            job = self._wait(client, response.json()["job_id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
            search_id = job["result"]["search_id"]
            detail = client.get(f"/v1/lead-finding/searches/{search_id}").json()
            self.assertEqual(detail["found_count"], 3)
            selected = [item["id"] for item in detail["leads"][:2]]

            previews = client.get("/v1/leads").json()["items"]
            self.assertEqual(
                {lead["id"] for lead in previews},
                {item["id"] for item in detail["leads"]},
            )
            self.assertTrue(all(lead["state"] == NEW for lead in previews))
            self.assertTrue(
                all(lead["metadata"]["search_preview"] for lead in previews)
            )

            research = client.post(
                "/v1/research",
                json={"lead_id": selected[0], "prompt": "Check current priorities."},
            )
            self.assertEqual(research.status_code, 202, research.text)
            research_job = self._wait(client, research.json()["job_id"])
            self.assertEqual(
                research_job["status"],
                "succeeded",
                research_job["error"],
            )

            imported = client.post(
                f"/v1/lead-finding/searches/{search_id}/import",
                json={"lead_ids": selected},
            )
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()["imported_count"], 2)
            leads = client.get("/v1/leads").json()["items"]
            by_id = {lead["id"]: lead for lead in leads}
            self.assertEqual(set(by_id), {item["id"] for item in detail["leads"]})
            for lead_id in selected:
                self.assertEqual(by_id[lead_id]["state"], DISCOVERED)
                self.assertFalse(by_id[lead_id]["metadata"]["search_preview"])
                self.assertTrue(by_id[lead_id]["metadata"]["imported"])
            unselected = next(lead for lead in leads if lead["id"] not in selected)
            self.assertEqual(unselected["state"], NEW)
            self.assertTrue(unselected["metadata"]["search_preview"])

    def test_operator_messages_create_conversation_and_timeline(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.post(
                "/v1/orchestrator/messages",
                json={"message": "Show me the current pipeline report."},
            )
            self.assertEqual(response.status_code, 202, response.text)
            payload = response.json()
            job = self._wait(client, payload["job_id"])
            self.assertEqual(job["status"], "succeeded", job["error"])

            conversation = client.get(
                f"/v1/orchestrator/conversations/{payload['conversation_id']}"
            )
            self.assertEqual(conversation.status_code, 200)
            roles = [message["role"] for message in conversation.json()["messages"]]
            self.assertEqual(roles, ["user", "tool", "agent"])
            timeline = client.get(
                f"/v1/orchestrator/conversations/{payload['conversation_id']}/timeline"
            ).json()
            self.assertEqual(timeline[0]["status"], "succeeded")
            self.assertEqual(timeline[0]["action"], "pipeline.report")


if __name__ == "__main__":
    unittest.main()
