"""
Component test for Agent 3: Email Writer.

Runs the EmailWriterAgent orchestration with deterministic in-process fakes at
the external boundaries:
  - ResearchReader: returns one Agent 1/Agent 2 lead pair
  - WritingLayer: returns a complete draft without calling an LLM
  - QualityLayer: returns a passing quality report without calling an LLM

The real InputAssembler, EmailWriterAgent.run/_process_lead, EmailDatabase, and
SQLite schema are exercised end-to-end.

Run from the repo root:
    .venv/bin/python agents/agent3-email-writer/run_component_test.py
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = AGENT_DIR.parent.parent
PKG_NAME = "agent3_email_writer"


def _import_agent_pkg():
    """Load the hyphenated agent dir as the package `agent3_email_writer`."""
    sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        AGENT_DIR / "__init__.py",
        submodule_search_locations=[str(AGENT_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load package from {AGENT_DIR}")

    pkg = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = pkg
    spec.loader.exec_module(pkg)
    return pkg


class FakeResearchReader:
    def __init__(self, pairs: list[tuple[dict, dict]]) -> None:
        self._pairs = pairs

    def read_all(self, min_quality_score: float = 5.0) -> list[tuple[dict, dict]]:
        return self._pairs

    def read_by_lead_ids(self, lead_ids: list[str]) -> list[tuple[dict, dict]]:
        wanted = set(lead_ids)
        return [
            (research, lead)
            for research, lead in self._pairs
            if lead.get("id") in wanted
        ]


class FakeWritingLayer:
    async def write(self, ctx):
        models = importlib.import_module(f"{PKG_NAME}.models")
        full_body = "\n\n".join(
            [
                f"Hi {ctx.lead_first_name},",
                "Noticed Chengdu Robotics is hiring sales engineers while expanding into APAC.",
                (
                    "AutoReach helps teams turn recent company research into concise outbound "
                    "emails, so your reps can spend more time with qualified conversations."
                ),
                "Would a 15 minute walkthrough next week be useful?",
                ctx.sender.signature,
            ]
        )
        return models.EmailDraft(
            subject="Chengdu Robotics outbound expansion",
            hook="Noticed Chengdu Robotics is hiring sales engineers while expanding into APAC.",
            body=(
                "AutoReach helps teams turn recent company research into concise outbound "
                "emails, so your reps can spend more time with qualified conversations."
            ),
            cta="Would a 15 minute walkthrough next week be useful?",
            full_body=full_body,
            word_count=len(full_body.split()),
        )


class FakeQualityLayer:
    async def check(self, draft, ctx):
        models = importlib.import_module(f"{PKG_NAME}.models")
        report = models.QualityReport(
            checks=[
                models.QualityCheck(
                    checker="component",
                    passed=True,
                    score=1.0,
                    issues=[],
                    suggestions=[],
                )
            ]
        )
        report.compute_overall()
        return report

    async def revise(self, draft, ctx, report):
        raise AssertionError("Passing quality report should not trigger revision")


def _sample_research_profile() -> dict:
    return {
        "id": "research-chengdu-001",
        "lead_id": "lead-chengdu-001",
        "status": "complete",
        "quality_score": {"score": 8.5},
        "company_profile": {
            "summary": "Chengdu Robotics builds warehouse automation systems for APAC manufacturers."
        },
        "pain_points": [
            {
                "title": "Outbound expansion",
                "description": "The sales team is entering new APAC markets.",
                "severity": "high",
                "evidence": "Recent hiring posts mention outbound sales engineering roles.",
            }
        ],
        "personal_profile": {
            "personal_hooks": [
                "Shared a hiring post for APAC sales engineers last week."
            ]
        },
        "email_angle": {
            "best_hook": "Noticed Chengdu Robotics is expanding outbound in APAC.",
            "recommended_cta": "Would a 15 minute walkthrough next week be useful?",
            "tone": "conversational",
            "subject_lines": ["Chengdu Robotics outbound expansion"],
        },
    }


def _sample_lead() -> dict:
    return {
        "id": "lead-chengdu-001",
        "first_name": "Mei",
        "last_name": "Lin",
        "title": "VP Sales",
        "company_name": "Chengdu Robotics",
    }


def _assert_job_row(db_path: Path, job_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, total, written, quality_passed, quality_failed, skipped "
            "FROM email_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

    assert row == ("complete", 1, 1, 1, 0, 0), f"Unexpected job row: {row!r}"


async def main() -> None:
    _import_agent_pkg()

    agent_mod = importlib.import_module(f"{PKG_NAME}.agent")
    config_mod = importlib.import_module(f"{PKG_NAME}.config")
    models = importlib.import_module(f"{PKG_NAME}.models")
    input_mod = importlib.import_module(f"{PKG_NAME}.layers.input.assembler")
    db_mod = importlib.import_module(f"{PKG_NAME}.layers.output.email_db")

    sender = models.SenderProfile(
        first_name="Januda",
        last_name="Lelwala",
        title="Founder",
        company="AutoReach",
        email="januda@example.com",
        signature="Januda Lelwala\nFounder\nAutoReach",
    )
    brand_voice = models.BrandVoice(
        company_name="AutoReach",
        tone="conversational",
        value_proposition="AI-powered outbound emails based on real lead research",
        forbidden_phrases=["I hope this email finds you well"],
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "emails.db"
        config = config_mod.ServiceConfig(
            db_path=str(db_path),
            concurrency=2,
            max_revision_attempts=0,
        )

        agent = agent_mod.EmailWriterAgent.__new__(agent_mod.EmailWriterAgent)
        agent._config = config
        agent._reader = FakeResearchReader([(_sample_research_profile(), _sample_lead())])
        agent._assembler = input_mod.InputAssembler(brand_voice, sender)
        agent._writing = FakeWritingLayer()
        agent._quality = FakeQualityLayer()
        agent._db = db_mod.EmailDatabase(str(db_path))

        job_id = "component-email-writer-job"
        job = await agent.run(job_id=job_id)
        emails = agent._db.get_emails_by_job(job_id)
        agent._db.close()

        assert job.status == "complete", job
        assert job.total == 1, job
        assert job.written == 1, job
        assert job.quality_passed == 1, job
        assert job.quality_failed == 0, job
        assert job.skipped == 0, job

        assert len(emails) == 1, emails
        email = emails[0]
        assert email["lead_id"] == "lead-chengdu-001", email
        assert email["research_profile_id"] == "research-chengdu-001", email
        assert email["status"] == "approved", email
        assert email["quality_passed"] == 1, email
        assert email["subject"] == "Chengdu Robotics outbound expansion", email
        assert "Hi Mei" in email["body"], email
        assert "Chengdu Robotics" in email["body"], email
        assert "Januda Lelwala" in email["body"], email

        _assert_job_row(db_path, job_id)

    print("PASS Agent 3 EmailWriter component test")
    print(f"job_id={job_id} total={job.total} written={job.written} approved={len(emails)}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    asyncio.run(main())
