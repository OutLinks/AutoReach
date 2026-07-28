from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from orchestrator.adapters.live import _load_agent, _relax_simulated_pacing
from orchestrator.campaigns import (
    AgentInstructions,
    CampaignBrief,
    CampaignSendPolicy,
)
from orchestrator.config import OrchestratorConfig
from orchestrator.models import CLOSED, READY
from orchestrator.responsibilities.decide import Decide
from orchestrator.responsibilities.monitor import Monitor
from orchestrator.responsibilities.optimize import Optimize
from orchestrator.state_machine import FOLLOWUP


class WebScraperContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _load_agent("agent1-lead-finder", "agent1_lead_finder")
        cls.search = importlib.import_module(
            "agent1_lead_finder.engines.search.web_scraper"
        )
        cls.models = importlib.import_module("agent1_lead_finder.models")

    def test_explicit_sources_are_preserved_before_discovered_links(self) -> None:
        ai_ceylon = "https://www.aiceylon.com/careers"
        bjak = "https://bjak.com/careers"
        bjak_contact = "https://bjak.com/contact"

        ai_page = self.search._PageParser()
        ai_page.feed(
            """
            <title>Careers — AI Ceylon — AI Ceylon</title>
            <meta property="og:site_name" content="AI Ceylon">
            <meta name="description" content="AI services company">
            <a href="https://unrelated.example/jobs">external</a>
            hello@aiceylon.com
            """
        )
        bjak_page = self.search._PageParser()
        bjak_page.feed(
            """
            <title>Bjak Careers</title>
            <meta name="description" content="Financial technology company">
            <a href="/contact">Contact</a>
            """
        )
        bjak_contact_page = self.search._PageParser()
        bjak_contact_page.feed("Contact hello@bjak.com")
        pages = {
            ai_ceylon: ai_page,
            bjak: bjak_page,
            bjak_contact: bjak_contact_page,
        }

        async def fake_fetch(_client, url):
            return pages.get(url)

        async def run():
            with patch.object(
                self.search.WebScraperAdapter,
                "_fetch",
                new=staticmethod(fake_fetch),
            ):
                return await self.search.WebScraperAdapter().search(
                    self.models.SearchCriteria(max_results=2),
                    [ai_ceylon, bjak],
                    2,
                )

        leads = asyncio.run(run())
        self.assertEqual([lead.company_name for lead in leads], ["AI Ceylon", "Bjak"])
        self.assertEqual(leads[0].email, "hello@aiceylon.com")
        self.assertEqual(leads[1].email, "hello@bjak.com")
        self.assertEqual(
            leads[1].company_description,
            "Financial technology company",
        )
        self.assertEqual([lead.company_website for lead in leads], [ai_ceylon, bjak])

    def test_scraper_rejects_third_party_email_and_navigation_domains(self) -> None:
        page = self.search._PageParser()
        page.feed(
            """
            <title>Example Company</title>
            <meta name="description" content="Example description">
            <a href="https://startupschool.org/">Navigation</a>
            unrelated@gmail.com
            """
        )

        self.assertEqual(
            self.search.WebScraperAdapter._external_company_links(
                "https://example.com",
                page.links,
            ),
            [],
        )
        self.assertIsNone(
            self.search.WebScraperAdapter._map_pages(
                "https://example.com",
                [("https://example.com", page)],
                self.models.SearchCriteria(),
            )
        )

    def test_known_compound_brand_capitalization_is_preserved(self) -> None:
        self.assertEqual(
            self.search.WebScraperAdapter._company_name_hint(
                "https://jobs.ashbyhq.com/openrouter/job-id"
            ),
            "OpenRouter",
        )


class SearchEngineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent1 = _load_agent("agent1-lead-finder", "agent1_lead_finder")
        cls.engine = importlib.import_module(
            "agent1_lead_finder.engines.search.engine"
        )

    def test_tavily_candidates_are_scraped_before_becoming_leads(self) -> None:
        raw_result = self.agent1.Lead(
            company_name="Search result",
            company_website="https://example.com",
            company_description="Unverified search snippet",
            sources=["tavily"],
        )
        scraped_lead = self.agent1.Lead(
            company_name="Example",
            company_website="https://example.com/",
            company_domain="example.com",
            company_description="Public company description",
            email="hello@example.com",
            sources=["web_scraper"],
        )
        tavily = SimpleNamespace(search=AsyncMock(return_value=[raw_result]))
        scraper = SimpleNamespace(
            search=AsyncMock(return_value=[]),
            scrape_companies=AsyncMock(return_value=[scraped_lead]),
        )
        store = SimpleNamespace(push_leads=AsyncMock())
        config = self.agent1.ServiceConfig()
        config.tavily.api_key = "test-key"
        config.tavily.enabled = True
        config.web_scraper_seed_urls = []

        async def run():
            with (
                patch.object(self.engine, "TavilyAdapter", return_value=tavily),
                patch.object(self.engine, "WebScraperAdapter", return_value=scraper),
            ):
                return await self.engine.SearchEngine(config, store).run(
                    self.agent1.SearchCriteria(max_results=3),
                    "web-search-job",
                )

        leads = asyncio.run(run())

        self.assertEqual(leads, [scraped_lead])
        scraper.scrape_companies.assert_awaited_once_with(
            ANY,
            ["https://example.com"],
            3,
        )
        self.assertIn("tavily", leads[0].sources)
        store.push_leads.assert_awaited_once_with(
            "web-search-job",
            "raw",
            leads,
        )


class CampaignInstructionContractTests(unittest.TestCase):
    def test_original_user_constraints_reach_every_agent(self) -> None:
        original = (
            "Write job-interest emails using only the AutoReach project. "
            "Do not invent credentials."
        )
        brief = CampaignBrief(
            user_prompt=original,
            source_urls=["https://example.com/careers"],
            agent_instructions=AgentInstructions(
                lead_finder="Find the companies.",
                research_analyst="Research the roles.",
                email_writer="Draft the emails.",
                sender="Prepare delivery.",
                reply_handler="Handle replies.",
            ),
        )

        for agent in (
            "lead_finder",
            "research_analyst",
            "email_writer",
            "sender",
            "reply_handler",
        ):
            self.assertIn(original, brief.instruction_for(agent))

        self.assertIn(
            "https://example.com/careers",
            brief.instruction_for("lead_finder"),
        )


class AgentHandoffContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent2 = _load_agent(
            "agent2-research-analyst", "agent2_research_analyst"
        )
        cls.agent2_module = importlib.import_module("agent2_research_analyst.agent")
        cls.writer_module = importlib.import_module(
            "agent2_research_analyst.layers.output.writer"
        )
        cls.website_module = importlib.import_module(
            "agent2_research_analyst.layers.collection.website"
        )
        cls.agent2_config = importlib.import_module(
            "agent2_research_analyst.config"
        )
        cls.models = importlib.import_module("agent2_research_analyst.models")

    def test_explicit_lead_ids_bypass_sales_grade_filter(self) -> None:
        low_grade_lead = {"id": "lead-d", "lead_grade": "D"}
        reader = MagicMock()
        reader.read_by_id.return_value = low_grade_lead
        agent = object.__new__(self.agent2_module.ResearchAgent)

        with patch.object(self.agent2_module, "LeadReader", return_value=reader):
            leads = agent._load_leads(["lead-d"], "B", None)

        self.assertEqual(leads, [low_grade_lead])
        reader.read_all.assert_not_called()

    def test_valid_research_profile_is_marked_complete_before_persisting(self) -> None:
        store = MagicMock()
        writer = self.writer_module.OutputWriter(store)
        profile = self.models.ResearchProfile(lead_id="lead-1")
        job = self.models.ResearchJob(id="job-1", total=1)

        with patch.object(self.writer_module, "validate", return_value=(True, "")):
            writer.write(profile, job)

        self.assertEqual(profile.status, "complete")
        self.assertEqual(job.completed, 1)
        store.write.assert_called_once_with(profile)

    def test_firecrawl_request_uses_current_v1_fields(self) -> None:
        config = self.agent2_config.ServiceConfig()
        config.firecrawl.api_key = "test-key"
        scraper = self.website_module.WebsiteScraper(config)
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": {"markdown": "role details"}}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)

        content = asyncio.run(
            scraper._scrape_page(client, "https://example.com/careers")
        )

        self.assertEqual(content, "role details")
        request_body = client.post.await_args.kwargs["json"]
        self.assertNotIn("removeTags", request_body)
        self.assertTrue(request_body["onlyMainContent"])

    def test_specific_job_urls_are_scraped_without_appended_site_pages(self) -> None:
        config = self.agent2_config.ServiceConfig()
        scraper = self.website_module.WebsiteScraper(config)

        self.assertEqual(
            scraper._pages_to_try("https://jobs.ashbyhq.com/acme/job-id"),
            [""],
        )
        self.assertEqual(
            scraper._pages_to_try("https://example.com/careers"),
            [""],
        )
        self.assertGreater(len(scraper._pages_to_try("https://example.com")), 1)

    def test_removed_job_page_is_not_treated_as_research_evidence(self) -> None:
        config = self.agent2_config.ServiceConfig()
        config.firecrawl.api_key = "test-key"
        scraper = self.website_module.WebsiteScraper(config)
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "data": {"markdown": "# Job not found\nView all open positions"}
        }
        client = MagicMock()
        client.post = AsyncMock(return_value=response)

        content = asyncio.run(
            scraper._scrape_page(client, "https://jobs.example.com/removed")
        )

        self.assertIsNone(content)


class EmailWritingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _load_agent("agent3-email-writer", "agent3_email_writer")
        cls.models = importlib.import_module("agent3_email_writer.models")
        cls.assembler_module = importlib.import_module(
            "agent3_email_writer.layers.input.assembler"
        )
        cls.writer_module = importlib.import_module(
            "agent3_email_writer.layers.writing.writer"
        )
        cls.prompt_module = importlib.import_module("agent3_email_writer.prompt")
        cls.sender_module = importlib.import_module(
            "agent3_email_writer.layers.input.sender_profile"
        )

    def _context(self):
        voice = self.models.BrandVoice(
            company_name="AutoReach",
            tone="conversational",
            value_proposition="automated sales outreach",
        )
        sender = self.models.SenderProfile(
            first_name="Januda",
            last_name="Lelwala",
            title="AI Engineer",
            company="AutoReach",
            email="januda@example.com",
        )
        assembler = self.assembler_module.InputAssembler(voice, sender)
        return assembler.assemble(
            research_profile={
                "id": "research-1",
                "company_profile": {"summary": "Acme is hiring an AI engineer."},
                "raw_data": {
                    "website_pages": {
                        "homepage": (
                            "# Applied AI Engineer\n"
                            "Build internal agentic tooling for support."
                        )
                    }
                },
            },
            lead={
                "id": "lead-1",
                "title": None,
                "company_name": "Acme",
                "full_name": None,
            },
            campaign_instruction=(
                "Write a truthful job-interest email from Januda for the AI Engineer role. "
                "Candidate evidence is limited to the AutoReach project. Describe only "
                "this project evidence: asynchronous Python multi-agent orchestration, "
                "FastAPI, Redis, provider-neutral LLM adapters, durable jobs, "
                "reliability controls, and Docker."
            ),
        )

    def test_null_lead_fields_are_normalized_and_greeting_is_natural(self) -> None:
        ctx = self._context()

        self.assertEqual(ctx.lead_title, "")
        self.assertEqual(ctx.lead_first_name, "")
        self.assertIn("internal agentic tooling", ctx.source_evidence)
        body = self.writer_module._assemble("", "Hook.", "Body.", "CTA.", "Januda")
        self.assertTrue(body.startswith("Hi there,\n"))

    def test_campaign_purpose_is_authoritative_in_writer_system_prompts(self) -> None:
        ctx = self._context()
        systems = [
            self.prompt_module.build_subject_prompt(ctx)[0],
            self.prompt_module.build_hook_prompt(ctx, "AI Engineer role")[0],
            self.prompt_module.build_body_prompt(
                ctx, "AI Engineer role", "Acme is hiring."
            )[0],
            self.prompt_module.build_cta_prompt(ctx, "Relevant experience.")[0],
        ]

        for system in systems:
            self.assertIn("CAMPAIGN AUTHORITY", system)
            self.assertIn("job-interest email", system)
            self.assertIn("must never pitch", system)

    def test_job_candidate_sender_profile_does_not_require_a_company(self) -> None:
        values = {
            "SENDER_FIRST_NAME": "Januda",
            "SENDER_LAST_NAME": "Lelwala",
            "SENDER_TITLE": "",
            "SENDER_COMPANY": "",
            "SENDER_EMAIL": "januda@example.com",
        }
        with patch.dict("os.environ", values, clear=False):
            profile = self.sender_module.SenderProfileLoader().load()

        self.assertEqual(profile.full_name, "Januda Lelwala")
        self.assertEqual(profile.company, "")
        self.assertEqual(profile.signature, "Januda Lelwala")

    def test_closed_project_evidence_uses_deterministic_safe_job_copy(self) -> None:
        ctx = self._context()
        draft = self.writer_module._write_closed_project_email(ctx)
        text = draft.full_body.lower()

        self.assertEqual(draft.subject, "Interest in Applied AI Engineer at Acme")
        self.assertIn("my autoreach project uses", text)
        self.assertIn("internal agentic tooling", text)
        self.assertNotIn("my experience", text)
        self.assertNotIn("successfully", text)
        self.assertNotIn("scalable", text)
        self.assertGreaterEqual(draft.word_count, 60)


class SendingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent4 = _load_agent("agent4-sender", "agent4_sender")
        cls.agent_module = importlib.import_module("agent4_sender.agent")
        cls.models = importlib.import_module("agent4_sender.models")

    def test_simulated_pacing_allows_the_requested_batch_only_in_simulation(self) -> None:
        simulated = SimpleNamespace(
            simulate=True,
            burst_per_minute=3,
            min_seconds_between_same_domain=30,
        )
        live = SimpleNamespace(
            simulate=False,
            burst_per_minute=3,
            min_seconds_between_same_domain=30,
        )

        _relax_simulated_pacing(simulated, 5)
        _relax_simulated_pacing(live, 5)

        self.assertEqual(simulated.burst_per_minute, 5)
        self.assertEqual(simulated.min_seconds_between_same_domain, 0)
        self.assertEqual(live.burst_per_minute, 3)
        self.assertEqual(live.min_seconds_between_same_domain, 30)

    def test_sender_schedules_against_the_resolved_recipient(self) -> None:
        agent = object.__new__(self.agent_module.SenderAgent)
        agent._config = SimpleNamespace(simulate=True)
        agent._reputation = MagicMock()
        agent._reputation.can_email.return_value = (True, "")
        agent._scheduling = MagicMock()
        agent._scheduling.schedule.return_value = None
        job = self.models.SendJob(kind="initial", total=1, status="in_progress")

        asyncio.run(
            agent._send_initial(
                {
                    "id": "email-1",
                    "lead_id": "lead-1",
                    "recipient": "",
                    "sender_email": "sender@example.com",
                },
                job,
            )
        )

        scheduled_email = agent._scheduling.schedule.call_args.args[0]
        self.assertEqual(scheduled_email["recipient"], "simulated@lead-1.invalid")

    def test_capacity_skip_remains_ready_for_retry(self) -> None:
        decide = Decide(OrchestratorConfig())

        self.assertEqual(decide._send("not_sent"), READY)
        self.assertEqual(decide._send("bounced"), CLOSED)

    def test_disabled_followups_create_no_sender_work(self) -> None:
        agent = object.__new__(self.agent_module.SenderAgent)
        agent._config = SimpleNamespace(followups_enabled=False)
        agent._store = MagicMock()
        agent._sequence = MagicMock()

        job = asyncio.run(agent.run_followups(job_id="no-followups"))

        self.assertEqual(job.status, "complete")
        self.assertEqual(job.total, 0)
        agent._sequence.due.assert_not_called()

    def test_monitor_excludes_campaigns_with_no_followups(self) -> None:
        campaign = CampaignBrief(
            id="campaign-no-followups",
            user_prompt="Send one email and no followups.",
            send_policy=CampaignSendPolicy(followup_days=[]),
            agent_instructions=AgentInstructions(
                lead_finder="Find.",
                research_analyst="Research.",
                email_writer="Write.",
                sender="Send.",
                reply_handler="Handle.",
            ),
        )
        store = MagicMock()
        store.leads_in_state.return_value = [
            SimpleNamespace(source_job=campaign.id)
        ]
        store.get_campaign.return_value = campaign
        monitor = Monitor(OrchestratorConfig(), store)

        self.assertEqual(monitor._waiting_leads(FOLLOWUP), [])

    def test_optimizer_counts_only_audited_bounces_as_bounces(self) -> None:
        store = MagicMock()
        store.count_events_with_note.side_effect = lambda note: {
            "send:bounced": 1,
            "send:sent": 9,
        }[note]
        optimizer = Optimize(OrchestratorConfig(), store)

        self.assertEqual(optimizer._bounce_rate(), 0.1)
        store.recent_runs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
