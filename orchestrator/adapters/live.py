"""
Live adapters.

Drive the real agents and reconcile the master store from the artifacts they
produce. Each agent lives in a hyphenated directory (not a normal Python package
name), so it's loaded via importlib under an underscore alias.

Live adapters TRIGGER an agent (its async batch entry point) and then RECONCILE:
read the produced JSONL / SQLite to determine which leads advanced and with what
outcome. Reconciliation is defensive — a missing artifact degrades to "processed
== advanced" rather than crashing the cycle. Used when simulate is False.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import agent_output_dir

from ..config import OrchestratorConfig
from ..campaigns import CampaignBrief
from ..models import DISCOVERED, PipelineLead, REPLIED, SENT, FOLLOWING_UP, StageResult
from ..state_machine import FIND, FOLLOWUP, REPLY, RESEARCH, SEND, WRITE, Stage
from ..store import OrchestratorStore
from .base import AgentAdapter, StageContext

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


def _load_agent(dirname: str, alias: str) -> Any:
    """Import a hyphenated agent package under an importable alias."""
    if alias in sys.modules:
        return sys.modules[alias]
    pkg = _AGENTS_DIR / dirname
    spec = importlib.util.spec_from_file_location(
        alias, pkg / "__init__.py", submodule_search_locations=[str(pkg)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load agent package {dirname}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


class LiveAdapter(AgentAdapter):
    """One adapter that dispatches per stage to the matching real agent."""

    def __init__(self, stage: Stage) -> None:
        super().__init__(stage)

    async def execute(self, ctx: StageContext) -> StageResult:
        start = time.monotonic()
        handler = {
            FIND.name: self._find,
            RESEARCH.name: self._research,
            WRITE.name: self._write,
            SEND.name: self._send,
            FOLLOWUP.name: self._followup,
            REPLY.name: self._reply,
        }[self.stage.name]
        result = await handler(ctx)
        result.duration_seconds = time.monotonic() - start
        return result

    # ── find ───────────────────────────────────────────────────────────────────

    async def _find(self, ctx: StageContext) -> StageResult:
        mod = _load_agent("agent1-lead-finder", "agent1_lead_finder")
        agent = mod.LeadFinderAgent(mod.ServiceConfig())
        search_request = (
            ctx.campaign.instruction_for("lead_finder")
            if ctx.campaign
            else ctx.config.targeting.search_prompt
        )
        await agent.run(search_request)
        # Ingest whatever new leads landed in Agent 1's output.
        added = await self._ingest_new_leads(ctx.store, ctx.now, ctx.campaign)
        return StageResult(
            stage=self.stage.name, agent=self.stage.agent,
            processed=added, succeeded=added, new_lead_ids=[],
        )

    async def _ingest_new_leads(
        self,
        store: OrchestratorStore,
        now: datetime,
        campaign: CampaignBrief | None = None,
    ) -> int:
        out = agent_output_dir("agent1-lead-finder")
        known = {lead.id for lead in store.all_leads()}
        added = 0
        for path in sorted(out.glob("leads_*.jsonl")):
            for lead in _stream_jsonl(path):
                lid = lead.get("id")
                if not lid or lid in known:
                    continue
                store.upsert_lead(PipelineLead(
                    id=lid, state=DISCOVERED,
                    email=lead.get("email") or "",
                    company=lead.get("company_name") or "",
                    industry=lead.get("industry") or "",
                    quality_score=(lead.get("lead_score") or 0.0) / 100.0,
                    discovered_at=now,
                    source_job=campaign.id if campaign else "",
                    metadata={"campaign_id": campaign.id} if campaign else {},
                ))
                known.add(lid)
                added += 1
        logger.info("LiveAdapter[find]: ingested %d new leads", added)
        return added

    # ── research ────────────────────────────────────────────────────────────────

    async def _research(self, ctx: StageContext) -> StageResult:
        mod = _load_agent("agent2-research-analyst", "agent2_research_analyst")
        config = mod.ServiceConfig()
        if ctx.campaign:
            config.campaign_instruction = ctx.campaign.instruction_for("research_analyst")
        agent = mod.ResearchAgent(config)
        await agent.run(lead_ids=ctx.lead_ids)
        # Reconcile from research JSONL: status complete/partial → ok.
        out = agent_output_dir("agent2-research-analyst")
        statuses = _index_jsonl(out.glob("research_*.jsonl"), key="lead_id")
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            profile = statuses.get(lid)
            ok = bool(profile) and profile.get("status") in ("complete", "partial")
            result.outcomes[lid] = "ok" if ok else "incomplete"
            if ok:
                result.advanced_ids.append(lid)
        result.processed = len(ctx.lead_ids)
        result.succeeded = len(result.advanced_ids)
        result.failed = result.processed - result.succeeded
        return result

    # ── write ───────────────────────────────────────────────────────────────────

    async def _write(self, ctx: StageContext) -> StageResult:
        mod = _load_agent("agent3-email-writer", "agent3_email_writer")
        config = mod.ServiceConfig()
        if ctx.campaign:
            config.campaign_instruction = ctx.campaign.instruction_for("email_writer")
        agent = mod.EmailWriterAgent(config)
        await agent.run(lead_ids=ctx.lead_ids)
        db = agent_output_dir("agent3-email-writer") / "emails.db"
        rows = _read_db(db, "SELECT lead_id, status, quality_score FROM emails", "lead_id")
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            row = rows.get(lid)
            approved = bool(row) and row.get("status") == "approved"
            score = (row or {}).get("quality_score") or 0.0
            result.outcomes[lid] = f"email_score={score:.2f}" if approved else "low_quality"
            if approved:
                result.advanced_ids.append(lid)
        result.processed = len(ctx.lead_ids)
        result.succeeded = len(result.advanced_ids)
        result.failed = result.processed - result.succeeded
        return result

    # ── send ────────────────────────────────────────────────────────────────────

    async def _send(self, ctx: StageContext) -> StageResult:
        mod = _load_agent("agent4-sender", "agent4_sender")
        config = mod.ServiceConfig.from_env()
        if ctx.campaign:
            config.campaign_instruction = ctx.campaign.instruction_for("sender")
            config.followups_enabled = bool(ctx.campaign.send_policy.followup_days)
            config.daily_send_limit = min(
                config.daily_send_limit, ctx.campaign.send_policy.emails_per_day
            )
            config.hourly_send_limit = min(
                config.hourly_send_limit, ctx.campaign.send_policy.hourly_send_limit
            )
        _relax_simulated_pacing(config, len(ctx.lead_ids))
        agent = mod.SenderAgent(config)
        await agent.run_initial(lead_ids=ctx.lead_ids)
        db = agent_output_dir("agent4-sender") / "sends.db"
        rows = _read_db(db, "SELECT lead_id, status, bounced FROM sent_emails", "lead_id")
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            row = rows.get(lid)
            bounced = bool(row) and row.get("bounced")
            delivered = bool(row) and row.get("status") in {
                "sent", "delivered", "opened", "clicked", "replied"
            }
            if bounced:
                result.outcomes[lid] = "bounced"
                result.failed += 1
            elif delivered:
                result.outcomes[lid] = "sent"
                result.advanced_ids.append(lid)
            else:
                result.outcomes[lid] = "not_sent"
        result.processed = len(ctx.lead_ids)
        result.succeeded = len(result.advanced_ids)
        return result

    # ── followup ────────────────────────────────────────────────────────────────

    async def _followup(self, ctx: StageContext) -> StageResult:
        mod = _load_agent("agent4-sender", "agent4_sender")
        config = mod.ServiceConfig.from_env()
        if ctx.campaign:
            config.campaign_instruction = ctx.campaign.instruction_for("sender")
            config.followups_enabled = bool(ctx.campaign.send_policy.followup_days)
            config.daily_send_limit = min(
                config.daily_send_limit, ctx.campaign.send_policy.emails_per_day
            )
            config.hourly_send_limit = min(
                config.hourly_send_limit, ctx.campaign.send_policy.hourly_send_limit
            )
        agent = mod.SenderAgent(config)
        await agent.run_followups()
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            result.outcomes[lid] = "continued"
            result.advanced_ids.append(lid)
        result.processed = result.succeeded = len(ctx.lead_ids)
        return result

    # ── reply ───────────────────────────────────────────────────────────────────

    async def _reply(self, ctx: StageContext) -> StageResult:
        mod = _load_agent("agent5-reply-handler", "agent5_reply_handler")
        config = mod.ServiceConfig.from_env()
        if ctx.campaign:
            config.campaign_instruction = ctx.campaign.instruction_for("reply_handler")
        agent = mod.ReplyHandlerAgent(config)
        await agent.run()
        db = agent_output_dir("agent5-reply-handler") / "conversations.db"
        rows = _read_db(db, "SELECT id, status, escalated FROM conversations", "id")
        result = StageResult(stage=self.stage.name, agent=self.stage.agent)
        for lid in ctx.lead_ids:
            row = rows.get(lid) or {}
            status = row.get("status", "handled")
            if status == "meeting_booked":
                outcome = "meeting_booked"
            elif row.get("escalated"):
                outcome = "escalated"
            elif status in ("closed", "nurture"):
                outcome = "closed"
            else:
                outcome = "handled"
            result.outcomes[lid] = outcome
            result.advanced_ids.append(lid)
        result.processed = result.succeeded = len(ctx.lead_ids)
        return result

    async def ingest(
        self,
        store: OrchestratorStore,
        config: OrchestratorConfig,
        now: datetime | None = None,
    ) -> int:
        """For the reply stage: flip leads to REPLIED from Agent 4's hand-offs."""
        if self.stage.name != REPLY.name:
            return 0
        now = now or datetime.now(timezone.utc)
        replies_dir = agent_output_dir("agent4-sender") / "replies"
        if not replies_dir.exists():
            return 0
        flipped = 0
        for path in sorted(replies_dir.glob("reply_*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            lead = store.get_lead(payload.get("lead_id", ""))
            if lead and lead.state in (SENT, FOLLOWING_UP):
                prev = lead.state
                lead.state = REPLIED
                lead.replied_at = now
                lead.state_entered_at = now
                store.upsert_lead(lead)
                store.log_event(lead.id, prev, REPLIED, "reply hand-off")
                flipped += 1
        return flipped


# ── Artifact reading helpers ──────────────────────────────────────────────────

def _relax_simulated_pacing(config, batch_size: int) -> None:
    """Let simulation exercise a full batch without weakening live limits."""
    if not config.simulate:
        return
    config.burst_per_minute = max(config.burst_per_minute, batch_size)
    config.min_seconds_between_same_domain = 0


def _stream_jsonl(path: Path):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return


def _index_jsonl(paths, key: str) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for path in sorted(paths):
        for obj in _stream_jsonl(path):
            k = obj.get(key)
            if k:
                index[k] = obj
    return index


def _read_db(db_path: Path, query: str, key: str) -> dict[str, dict]:
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("LiveAdapter: db read failed (%s) — %s", db_path, exc)
        return {}
    return {r[key]: dict(r) for r in rows}
