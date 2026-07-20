"""Campaign planning models and the LLM compiler for user campaign requests."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from core.model_selection import Message, ModelConfig, get_model


class CampaignTargeting(BaseModel):
    industries: list[str] = Field(default_factory=list)
    company_sizes: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    job_titles: list[str] = Field(default_factory=list)
    exclude_industries: list[str] = Field(default_factory=list)
    b2b_only: bool = True


class CampaignMessaging(BaseModel):
    offer: str = ""
    value_proposition: str = ""
    tone: str = "professional"
    call_to_action: str = ""
    proof_points: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)


class CampaignSendPolicy(BaseModel):
    emails_per_day: int = Field(default=20, ge=1, le=100)
    hourly_send_limit: int = Field(default=5, ge=1, le=25)
    followup_days: list[int] = Field(default_factory=lambda: [3, 7, 14])


class AgentInstructions(BaseModel):
    """Focused instructions for each specialist, derived from one user brief."""

    lead_finder: str
    research_analyst: str
    email_writer: str
    sender: str
    reply_handler: str


class CampaignBrief(BaseModel):
    """Validated, auditable contract between the user and every pipeline agent."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "Untitled campaign"
    user_prompt: str
    summary: str = ""
    targeting: CampaignTargeting = Field(default_factory=CampaignTargeting)
    messaging: CampaignMessaging = Field(default_factory=CampaignMessaging)
    send_policy: CampaignSendPolicy = Field(default_factory=CampaignSendPolicy)
    # Crawl targets are trusted only when literally present in the user's prompt.
    source_urls: list[str] = Field(default_factory=list)
    agent_instructions: AgentInstructions
    status: str = "draft"  # draft | active | archived
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("source_urls")
    @classmethod
    def http_urls_only(cls, urls: list[str]) -> list[str]:
        return [url for url in urls if url.startswith(("https://", "http://"))]

    def instruction_for(self, agent: str) -> str:
        instructions = getattr(self.agent_instructions, agent)
        if agent == "lead_finder" and self.source_urls:
            sources = "\n".join(f"- {url}" for url in self.source_urls)
            return f"{instructions}\n\nPublic source URLs to scrape:\n{sources}"
        return instructions


_PLANNER_SYSTEM_PROMPT = """\
You are the Campaign Planner for a B2B outreach product. Convert one user's
free-form request into a precise campaign brief for specialised agents. You do
not run outreach, browse, or invent evidence.

Rules:
- Preserve the user's intent; use concise, actionable instructions.
- Default `b2b_only` to true unless the user clearly requests consumer outreach.
- Put excluded consumer categories in `exclude_industries` when relevant.
- Do not invent source URLs. The runtime supplies URLs that literally occurred
  in the user's request.
- Do not make promises, deceptive claims, or instructions to evade consent,
  unsubscribe, or sender-reputation safeguards.
- Agent instructions must be specific to that agent's responsibility, not copies
  of the whole user request. The sender instruction is operational only and
  must not override safety limits.
- Return only a JSON object matching this schema:
{schema}
"""


class CampaignPlanner:
    """One LLM call that compiles a user request into an auditable campaign."""

    def __init__(self, model: ModelConfig, adapter: Any | None = None) -> None:
        self._model = model
        self._adapter = adapter or get_model(model)

    async def plan(self, user_prompt: str) -> CampaignBrief:
        system = _PLANNER_SYSTEM_PROMPT.format(
            schema=json.dumps(CampaignBrief.model_json_schema(), indent=2)
        )
        response = await self._adapter.complete(
            self._model,
            [
                Message(role="system", content=system),
                Message(role="user", content=user_prompt),
            ],
        )
        data = json.loads(_extract_json(response.content or ""))
        brief = CampaignBrief.model_validate({
            **data,
            "user_prompt": user_prompt,
            # Planning never activates a campaign. Activation is an explicit,
            # reviewable human action through Orchestrator.activate_campaign().
            "status": "draft",
        })
        # Crawl sources are an input-security boundary: accept only URLs the
        # human supplied, rather than URLs an LLM might hallucinate.
        brief.source_urls = _extract_urls(user_prompt)
        brief.updated_at = datetime.utcnow()
        return brief


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    bare = re.search(r"\{.*\}", text, re.DOTALL)
    return bare.group(0) if bare else text


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>()\[\]{}\"']+", text)
    return list(dict.fromkeys(url.rstrip(".,;:!?)") for url in urls))
