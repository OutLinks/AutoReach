"""
Full Supabase-backed simulation for Agent 3: Email Writer.

What it does:
  1. Loads .env from the repo root and this agent directory if present.
  2. Seeds Supabase `leads` and `research_profiles` with UUID test records.
  3. Writes matching Agent 1/Agent 2 JSONL output files for Agent 3 to read.
  4. Executes EmailWriterAgent normally.
  5. Queries Supabase `emails` to prove the emails were stored remotely.

Prerequisites:
  - SUPABASE_URL and SUPABASE_SERVICE_KEY set.
  - OPENROUTER_API_KEY set, or edit the ModelConfig below.
  - supabase/schema.sql applied to the Supabase project.

Run from the repo root:
    .venv/bin/python agents/agent3-email-writer/run_supabase_simulated_test.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx

AGENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = AGENT_DIR.parent.parent
AGENTS_DIR = AGENT_DIR.parent
PKG_NAME = "agent3_email_writer"
sys.path.insert(0, str(REPO_ROOT))

from core.model_selection.types import ModelConfig

JOB_ID = str(uuid4())


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _import_agent_pkg():
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


def _require_env() -> tuple[str, str]:
    _load_env(REPO_ROOT / ".env")
    _load_env(AGENT_DIR / ".env")
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("ERROR: OPENROUTER_API_KEY must be set for this script.")
    return url.rstrip("/"), key


def _headers(key: str, prefer: str = "resolution=merge-duplicates,return=minimal") -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _post(url: str, key: str, table: str, records: list[dict]) -> None:
    endpoint = f"{url}/rest/v1/{table}"
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            endpoint,
            params={"on_conflict": "id"},
            headers=_headers(key),
            json=records,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Supabase seed failed for {table} "
                f"(HTTP {resp.status_code}): {resp.text[:500]}"
            )


def _get(url: str, key: str, table: str, params: dict) -> list[dict]:
    endpoint = f"{url}/rest/v1/{table}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(endpoint, headers=_headers(key, "return=representation"), params=params)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Supabase query failed for {table} "
                f"(HTTP {resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()


def _fixtures() -> tuple[list[dict], list[dict]]:
    lead1 = {
        "id": str(uuid4()),
        "first_name": "Aisha",
        "last_name": "Patel",
        "full_name": "Aisha Patel",
        "email": "aisha.patel@zenledgerops.example",
        "title": "Chief Revenue Officer",
        "company_name": "ZenLedgerOps",
        "company_website": "https://zenledgerops.example",
        "industry": "B2B SaaS",
    }
    lead2 = {
        "id": str(uuid4()),
        "first_name": "Marcus",
        "last_name": "Chen",
        "full_name": "Marcus Chen",
        "email": "marcus.chen@northstarfreight.example",
        "title": "VP of Sales",
        "company_name": "Northstar Freight",
        "company_website": "https://northstarfreight.example",
        "industry": "Logistics Software",
    }

    research1 = {
        "id": str(uuid4()),
        "lead_id": lead1["id"],
        "status": "complete",
        "quality_score": {"score": 8.8, "reason": "Strong outbound expansion signals."},
        "company_profile": {
            "summary": (
                "ZenLedgerOps is a B2B SaaS company helping finance teams automate "
                "month-end close and revenue reconciliation."
            )
        },
        "pain_points": [
            {
                "title": "Outbound personalization bottleneck",
                "description": (
                    "The revenue team is expanding into healthcare and fintech verticals, "
                    "but reps still write account research and first-touch emails manually."
                ),
                "severity": "high",
                "evidence": "Job posts mention vertical expansion and account-based prospecting.",
            }
        ],
        "personal_profile": {
            "personal_hooks": [
                "Aisha posted about building enterprise pipeline without adding SDR headcount."
            ]
        },
        "email_angle": {
            "best_hook": "Noticed ZenLedgerOps is hiring outbound AEs while pushing into healthcare finance teams.",
            "recommended_cta": "Would it be worth comparing notes for 15 minutes next week?",
            "tone": "conversational",
            "subject_lines": ["ZenLedgerOps healthcare outbound"],
        },
    }
    research2 = {
        "id": str(uuid4()),
        "lead_id": lead2["id"],
        "status": "complete",
        "quality_score": {"score": 8.3, "reason": "Clear regional carrier launch signal."},
        "company_profile": {
            "summary": (
                "Northstar Freight sells shipment visibility and routing software to "
                "mid-market logistics teams and recently launched a regional carrier product."
            )
        },
        "pain_points": [
            {
                "title": "Regional carrier expansion",
                "description": (
                    "The sales team is entering a fragmented carrier segment where each "
                    "account has different fleet, route, and service constraints."
                ),
                "severity": "high",
                "evidence": "Launch announcement highlights account-specific regional workflows.",
            }
        ],
        "personal_profile": {
            "personal_hooks": [
                "Marcus commented on a logistics podcast episode about carrier fragmentation."
            ]
        },
        "email_angle": {
            "best_hook": "Saw Northstar Freight's regional carrier launch and the push into more fragmented logistics accounts.",
            "recommended_cta": "Open to a quick look at how your reps could personalize first touches by carrier segment?",
            "tone": "direct",
            "subject_lines": ["Northstar's regional carrier push"],
        },
    }
    return [lead1, lead2], [research1, research2]


def _seed_supabase(url: str, key: str, leads: list[dict], research: list[dict]) -> None:
    leads_table = os.getenv("SUPABASE_LEADS_TABLE", "leads")
    research_table = os.getenv("SUPABASE_RESEARCH_TABLE", "research_profiles")
    lead_records = [
        {
            **lead,
            "job_id": JOB_ID,
            "stage": "researched",
            "payload": lead,
        }
        for lead in leads
    ]
    research_records = [
        {
            "id": item["id"],
            "lead_id": item["lead_id"],
            "job_id": JOB_ID,
            "status": item["status"],
            "quality_score": item["quality_score"],
            "profile": item,
        }
        for item in research
    ]
    _post(url, key, leads_table, lead_records)
    _post(url, key, research_table, research_records)


def _write_jsonl(leads: list[dict], research: list[dict]) -> None:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    leads_path = AGENTS_DIR / "agent1-lead-finder" / "output" / f"leads_supabase_sim_{timestamp}.jsonl"
    research_path = AGENTS_DIR / "agent2-research-analyst" / "output" / f"research_supabase_sim_{timestamp}.jsonl"
    leads_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.parent.mkdir(parents=True, exist_ok=True)
    leads_path.write_text(
        "\n".join(json.dumps(item) for item in leads) + "\n",
        encoding="utf-8",
    )
    research_path.write_text(
        "\n".join(json.dumps(item) for item in research) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote Agent 1 fixture: {leads_path}")
    print(f"Wrote Agent 2 fixture: {research_path}")


async def _run_agent(leads: list[dict]) -> object:
    pkg = _import_agent_pkg()
    config = pkg.ServiceConfig.from_env()
    config.model = ModelConfig(
        provider="openrouter",
        model="openai/gpt-4o-mini",
        max_tokens=800,
        temperature=0.55,
    )
    config.concurrency = 2
    config.max_revision_attempts = 1

    agent = pkg.EmailWriterAgent(config)
    try:
        return await agent.run(
            job_id=JOB_ID,
            lead_ids=[lead["id"] for lead in leads],
            min_quality_score=5.0,
        )
    finally:
        agent._db.close()


async def main() -> None:
    url, key = _require_env()
    os.environ.setdefault("SENDER_FIRST_NAME", "Januda")
    os.environ.setdefault("SENDER_LAST_NAME", "Lelwala")
    os.environ.setdefault("SENDER_TITLE", "Founder")
    os.environ.setdefault("SENDER_COMPANY", "AutoReach")
    os.environ.setdefault("SENDER_EMAIL", "januda@example.com")
    os.environ.setdefault("SENDER_SIGNATURE", "Januda Lelwala\nFounder, AutoReach")

    leads, research = _fixtures()
    _seed_supabase(url, key, leads, research)
    _write_jsonl(leads, research)
    job = await _run_agent(leads)
    rows = _get(
        url,
        key,
        os.getenv("SUPABASE_EMAILS_TABLE", "emails"),
        {
            "select": "id,lead_id,research_profile_id,subject,status,quality_score,quality_passed,job_id",
            "job_id": f"eq.{JOB_ID}",
            "order": "created_at.asc",
        },
    )

    print("\nJOB")
    print(job.model_dump())
    print(f"\nSUPABASE_EMAIL_ROWS {len(rows)}")
    for row in rows:
        print(json.dumps(row, indent=2))

    if len(rows) != len(leads):
        raise SystemExit(f"ERROR: expected {len(leads)} Supabase emails, got {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
