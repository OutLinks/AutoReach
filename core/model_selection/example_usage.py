"""
Example: how each agent wires up its own ModelConfig.

This file is for documentation only — not imported anywhere.
"""

from __future__ import annotations

import asyncio

from core.model_selection import (
    Message,
    ModelConfig,
    ToolDefinition,
    get_model,
)

# ── Agent 1: Lead Finder — cheap + fast for structured extraction ──────────────
AGENT1_CONFIG = ModelConfig(
    provider="openai",
    model="gpt-4o-mini",
    max_tokens=4096,
    temperature=0.3,  # low temp for deterministic structured output
)

# ── Agent 2: Research Analyst — long context, nuanced reasoning ───────────────
AGENT2_CONFIG = ModelConfig(
    provider="anthropic",
    model="claude-sonnet-4-6",
    max_tokens=8192,
    temperature=0.5,
)

# ── Agent 3: Email Writer — creative but consistent ───────────────────────────
AGENT3_CONFIG = ModelConfig(
    provider="anthropic",
    model="claude-sonnet-4-6",
    max_tokens=2048,
    temperature=0.8,  # slightly more creative for email writing
)

# ── Agent 4: Sender — lightweight scheduling logic ────────────────────────────
AGENT4_CONFIG = ModelConfig(
    provider="openai",
    model="gpt-4o-mini",
    max_tokens=1024,
    temperature=0.1,
)

# ── Agent 5: Reply Handler — nuanced intent classification ────────────────────
AGENT5_CONFIG = ModelConfig(
    provider="anthropic",
    model="claude-sonnet-4-6",
    max_tokens=4096,
    temperature=0.3,
)

# ── Usage pattern every agent follows ─────────────────────────────────────────

async def example_agent_call() -> None:
    adapter = get_model(AGENT2_CONFIG)

    messages = [
        Message(role="system", content="You are a research analyst."),
        Message(role="user", content="Summarise this company's pain points."),
    ]

    tools = [
        ToolDefinition(
            name="save_research",
            description="Save the research findings to the database",
            parameters={
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "pain_points": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "email_angle": {"type": "string"},
                    "lead_score": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["company_name", "pain_points", "email_angle", "lead_score"],
            },
        )
    ]

    response = await adapter.complete(AGENT2_CONFIG, messages, tools)

    if response.tool_calls:
        for tc in response.tool_calls:
            print(f"Tool called: {tc.name}")
            print(f"Arguments:   {tc.parsed_arguments()}")
    else:
        print(response.content)


# ── OpenRouter usage (access any model through one API key) ───────────────────

OPENROUTER_CONFIG = ModelConfig(
    provider="openrouter",
    model="anthropic/claude-sonnet-4.6",  # note: OpenRouter uses dot notation
    max_tokens=8192,
    extra={
        # OpenRouter-specific: force routing to Anthropic directly
        "extra_body": {
            "provider": {"order": ["Anthropic"], "allow_fallbacks": False}
        }
    },
)


if __name__ == "__main__":
    asyncio.run(example_agent_call())
