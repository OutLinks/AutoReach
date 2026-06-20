"""
Standalone smoke test for Agent 1: Lead Finder.

Runs the full pipeline against the natural-language prompt below. The LLM
defaults to anthropic / claude-sonnet-4-6; switch it purely via env vars
(LLM_PROVIDER / LLM_MODEL).

Prerequisites:
  - Redis running locally (redis-cli ping -> PONG)
  - agents/agent1-lead-finder/.env populated with:
        ANTHROPIC_API_KEY (or LLM_PROVIDER/LLM_MODEL + matching key),
        APOLLO_API_KEY, PRODUCTHUNT_API_KEY,
        REDIS_URL=redis://localhost:6379

  To test with OpenRouter owl-alpha, add to .env:
        LLM_PROVIDER=openrouter
        LLM_MODEL=openrouter/owl-alpha
        OPENROUTER_API_KEY=...

Run from the repo root:
    .venv/bin/python agents/agent1-lead-finder/run_test.py "Find 20 SaaS founders in SF"

The hyphenated package dir can't be imported with normal `import` syntax,
so this loads it via importlib under the alias `agent1_lead_finder`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(AGENT_DIR, "..", ".."))


def _load_env(path: str) -> None:
    """Minimal .env loader (avoids a python-dotenv dependency)."""
    if not os.path.exists(path):
        print(f"[warn] no .env at {path} — relying on shell environment")
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


def _import_agent_pkg():
    """Load the hyphenated agent dir as the package `agent1_lead_finder`."""
    sys.path.insert(0, REPO_ROOT)  # so `core.model_selection` resolves
    spec = importlib.util.spec_from_file_location(
        "agent1_lead_finder",
        os.path.join(AGENT_DIR, "__init__.py"),
        submodule_search_locations=[AGENT_DIR],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["agent1_lead_finder"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


async def main(prompt: str) -> None:
    pkg = _import_agent_pkg()

    config = pkg.ServiceConfig()
    print(f"LLM provider : {config.model.provider}")
    print(f"LLM model    : {config.model.model}")
    print(f"Search APIs  : {config.enabled_search_apis() or '(none ready!)'}")
    print(f"Enrich APIs  : {config.enabled_enrich_apis() or '(none)'}")
    print(f"Verify APIs  : {config.enabled_verify_apis() or '(none)'}")
    print(f"Redis        : {config.redis_url}")
    print("-" * 60)

    key_env = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(config.model.provider, "ANTHROPIC_API_KEY")
    if not os.environ.get(key_env):
        sys.exit(f"ERROR: {key_env} is not set — cannot call the {config.model.provider} LLM.")
    if not config.any_search_api_ready():
        sys.exit("ERROR: no search API is ready — set APOLLO_API_KEY or PRODUCTHUNT_API_KEY.")

    agent = pkg.LeadFinderAgent(config)
    job = await agent.run(prompt)

    print("-" * 60)
    print(f"Status   : {job.status}")
    print(f"Found    : {job.total_found}")
    print(f"Unique   : {job.total_unique}")
    print(f"Verified : {job.total_verified}")
    print(f"Enriched : {job.total_enriched}")
    print(f"Scored   : {job.total_scored}")
    print(f"Written  : {job.total_written}")
    if job.error:
        print(f"Error    : {job.error}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _load_env(os.path.join(AGENT_DIR, ".env"))
    user_prompt = sys.argv[1] if len(sys.argv) > 1 else "Find 20 SaaS founders in San Francisco"
    asyncio.run(main(user_prompt))
