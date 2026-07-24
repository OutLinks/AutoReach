# AutoReach — AI Agent Project Context

This document is the practical handoff for an AI agent working in this repository. It describes the implementation as it exists on **2026-07-20**, rather than being a product specification. For the broader intended design, also read [10-full-system-architecture.md](10-full-system-architecture.md).

## Purpose

AutoReach is a Python, asynchronous, multi-agent cold-outreach system. It is designed to:

1. discover and qualify companies/contacts;
2. research those leads;
3. draft personalized outbound email;
4. send, track, and follow up while protecting sender reputation; and
5. interpret replies, book meetings, or escalate to a human.

A central orchestrator owns the master lead lifecycle. Individual agents own their specialized processing and their own storage/artifacts.

## Repository snapshot

- Remote: `https://github.com/OutLinks/AutoReach.git`
- Current branch when this document was written: `Januda-lelwala/change-api-endpoints`
- Target comparison branch in this Conductor workspace: `origin/main`
- Latest commit: `be8e9a2` (`Add AWS SES sending provider and environment loading functionality`)
- Language/runtime: Python 3.10+ syntax is required (`X | None`, standard-library `asyncio`, `sqlite3`).
- Dependency manifest: [requirements.txt](requirements.txt). The repository now includes an authenticated FastAPI backend, a durable single-consumer job executor, an in-process scheduler, Docker/Render packaging, and a standard-library `unittest` suite. There is still no `pyproject.toml`, package installer configuration, migration system, or CI workflow.
- Current working tree was clean when this document was written.

## Top-level map

```text
core/                              Shared environment and LLM-provider abstraction
api/                               FastAPI routes, durable jobs, scheduler, runtime settings
orchestrator/                      Master lifecycle engine, queue control, health/reporting
agents/agent1-lead-finder/         Discovery, enrichment, verification, scoring, deduplication
agents/agent2-research-analyst/    Web/company/person research and email-angle analysis
agents/agent3-email-writer/        Personalized email drafting and quality gate
agents/agent4-sender/              Scheduling, send providers, tracking, sequences, reputation
agents/agent5-reply-handler/       Reply understanding, action, handoff, conversation memory
10-full-system-architecture.md     Detailed product/architecture narrative
AI_PROJECT_CONTEXT.md              This implementation handoff
DEPLOYMENT.md                      Local, Conductor, Docker, Render, and API usage guide
Dockerfile / render.yaml           Single-instance deployment packaging
```

## HTTP backend

The deployable entrypoint is `api.main:app`. All `/v1` routes use Bearer-token
authentication in production; `/healthz` remains public for hosting probes.
Campaign planning, pipeline stages, scheduled ticks, and normalized sender
events are stored as durable jobs in `api/jobs.py` and executed sequentially by
`api/executor.py`. Interrupted queued/running jobs are recovered after restart.

The sequential worker and one Uvicorn process are intentional: the agents still
share local JSON/SQLite artifacts. `AUTOREACH_DATA_DIR` redirects every agent,
orchestrator, and API database into one persistent root. Do not increase the
Uvicorn worker or service instance count until these stores are migrated to a
shared database/object store. See [DEPLOYMENT.md](DEPLOYMENT.md).

The agents' directories intentionally contain hyphens. They are not importable as ordinary Python module names; the live orchestrator loads them dynamically under underscore aliases (for example, `agent1_lead_finder`).

## End-to-end pipeline

```text
Agent 1                 Agent 2               Agent 3             Agent 4                 Agent 5
Find leads       ->     Research       ->     Write email   ->    Send/follow-up    ->     Handle reply
    DISCOVERED             RESEARCHED             READY                SENT                  HANDLED
                                                                      \                \
                                                                       \-> REPLIED ------> MEETING_BOOKED / CLOSED
```

The actual legal state machine is in [orchestrator/state_machine.py](orchestrator/state_machine.py); state constants and master-record fields are in [orchestrator/models.py](orchestrator/models.py).

| Stage | Owner | Input state | Normal outcome | Important branches |
| --- | --- | --- | --- | --- |
| `find` | Agent 1 | none | `DISCOVERED` | creates new leads |
| `research` | Agent 2 | `DISCOVERED` | `RESEARCHED` | incomplete research closes lead |
| `write` | Agent 3 | `RESEARCHED` | `READY` | score below threshold closes lead |
| `send` | Agent 4 | `READY` | `SENT` | bounce closes lead |
| `followup` | Agent 4 | `SENT` / `FOLLOWING_UP` | `FOLLOWING_UP` | exhaustion gives `NO_REPLY` then `CLOSED` |
| `reply` | Agent 5 | `REPLIED` | `HANDLED` | may produce `MEETING_BOOKED` or `CLOSED` |

Terminal states are `MEETING_BOOKED`, `CLOSED`, and `DEAD`. The master `PipelineLead.id` is meant to remain the join key through all five agents.

## Orchestrator

The public entry point is [orchestrator/orchestrator.py](orchestrator/orchestrator.py):

```python
from orchestrator import Orchestrator, OrchestratorConfig

orch = Orchestrator(OrchestratorConfig())  # simulation is on by default
await orch.run_find()
await orch.run_until_drained()
print(orch.health())
print(orch.report())
```

Useful methods are `run_find()`, `run_stage(stage)`, `run_cycle()`, `run_until_drained()`, `tick(now)`, `health()`, `report()`, and `bump(lead_id)`.

### Its six responsibilities

- **Trigger**: runs stages from a daily hour-to-stage plan (6 health; 7 find; 8 research; 9 write/send; 10 reply; 18 follow-up; 20 report).
- **Control**: selects prioritized batches, using `quality*0.4 + company_size*0.2 + industry_fit*0.2 + recency*0.2`; manual bumps take precedence.
- **Monitor**: calculates queue health/error rate and owns per-stage circuit breakers.
- **Decide**: converts agent outcome tags into lifecycle transitions and applies quality gates.
- **Optimize**: tunes based on stored outcome data.
- **Report**: produces funnel, send/reply/meeting, dead-letter, alert, and optimization summaries.

The default `OrchestratorConfig` targets 5–50-person real-estate/property-management firms in the San Francisco Bay Area; sends 50 initial emails/day; has a 0.70 email-quality threshold; and uses simulated adapters. Change [orchestrator/config.py](orchestrator/config.py), or provide a customized config in code, for a different campaign.

### Reliability behavior

- Transient failure retries use 30s, 120s, then 600s backoff; after three attempts the lead is dead-lettered.
- Queue-stage circuit breakers open after five consecutive stage failures and cool down for 300 seconds.
- Active-state timeout is 30 minutes; a queue wait above 24 hours is an alert.
- The master coordination DB is `orchestrator/output/orchestrator.db`; its tables are `leads`, `runs`, `events`, and `dead_letter`.

### Simulation versus live mode

`simulate=True` uses deterministic, hash-based adapters in [orchestrator/adapters/simulated.py](orchestrator/adapters/simulated.py). It makes repeatable fake leads and outcomes, needs no APIs, and is the safe starting point.

Set `OrchestratorConfig(simulate=False)` to use [orchestrator/adapters/live.py](orchestrator/adapters/live.py). Live adapters invoke the real agent batch APIs and reconcile their JSONL/SQLite/file outputs back into the master store. Live operation therefore requires the dependent agents, credentials, their output locations, and—in Agent 1's case—Redis to work.

## Shared model layer

[core/model_selection](core/model_selection) is the provider-neutral LLM interface. Agents supply a `ModelConfig`; `get_model(config)` resolves an adapter for `anthropic`, `openai`, or `openrouter`. Provider modules normalize messages, tool calls, usage, and responses behind `ProviderAdapter`.

Default agent configurations use `anthropic` / `claude-sonnet-4-6`, but each agent can select another supported model independently. Expected LLM variables are `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `OPENROUTER_API_KEY`.

[core/env.py](core/env.py) implements a small `.env` loader that walks upward from the current directory and does not overwrite existing process environment values. Agent 1 and Agent 2 call it at config-module import time. Agents 3–5 do **not** directly call it, so standalone runs of those agents should export environment variables in the shell or explicitly call `load_dotenv()` first.

## Agent implementation guide

### Agent 1 — Lead Finder

Entrypoint: `LeadFinderAgent.run(prompt, job_id=None)` in [agents/agent1-lead-finder/agent.py](agents/agent1-lead-finder/agent.py).

- Engines: search (`Google Places`, `Tavily`), enrich (`Hunter`, `Wappalyzer`, `Crunchbase`, domain intelligence), verify (`Abstract`), then score/deduplicate.
- Stores job data in Redis (`REDIS_URL`, default `redis://localhost:6379`) and produces lead artifacts under `agents/agent1-lead-finder/output/`, including `leads_*.jsonl` expected by the live adapter.
- Primary tuning/configuration: [agents/agent1-lead-finder/config.py](agents/agent1-lead-finder/config.py). API sections are enabled only when both enabled and credentialed.
- Lead data models: `Lead`, `SearchCriteria`, `SearchJob`.

### Agent 2 — Research Analyst

Entrypoint: `ResearchAgent.run(lead_ids=None, job_id=None)` in [agents/agent2-research-analyst/agent.py](agents/agent2-research-analyst/agent.py).

- Reads Agent 1 leads, collects site content, public-web results, news, GitHub signals, and technology data.
- Analyzes company/personal context, pain points, email angle, and quality; validates and writes `ResearchProfile` records.
- Uses Firecrawl, Tavily, GNews, GitHub, and Wappalyzer when configured; its expected reconciliation artifact is `output/research_*.jsonl` with `lead_id` and status `complete` or `partial`.
- Key limits: concurrency 3, 5 pages/site, 10 news articles, 8 web results; see [agents/agent2-research-analyst/config.py](agents/agent2-research-analyst/config.py).

### Agent 3 — Email Writer

Entrypoint: `EmailWriterAgent.run(lead_ids=None, job_id=None)` in [agents/agent3-email-writer/agent.py](agents/agent3-email-writer/agent.py).

- Assembles Agent 2 research, sender profile, brand voice, and templates.
- Generates hook, subject, body, and CTA; then evaluates personalization, spam risk, tone, and length. It can revise once.
- Persists `WrittenEmail` and `EmailJob` to `agents/agent3-email-writer/output/emails.db`; live orchestration advances only `emails.status == 'approved'` and reads `quality_score`.
- Defaults: 60–200 body words, 0.70 quality pass threshold, concurrency 5. Sender profile comes from `SENDER_*` variables or an input profile file; see [agents/agent3-email-writer/config.py](agents/agent3-email-writer/config.py).

### Agent 4 — Sender and Follow-Up Manager

Entrypoints in [agents/agent4-sender/agent.py](agents/agent4-sender/agent.py):

- `run_initial(job_id=None, lead_ids=None)` sends approved Agent 3 emails.
- `run_followups(job_id=None, now=None)` advances due sequence states.
- Webhook/event hooks: `handle_reply`, `handle_bounce`, `handle_complaint`, `record_open`, and `record_click`.

Its five layers are scheduling, sending, tracking, sequence, and reputation.

- Schedules for local 10:00, constrained to 9:00–17:00 and avoids Monday, Friday, and weekends by default.
- Uses per-account warmup/capacity, account rotation, domain spacing, suppression, bounce/complaint checks, and a day 0 / 3 / 7 / 14 sequence.
- Providers: simulated, SMTP, Gmail, Instantly, Outreach, and AWS SES. Set `AGENT4_PROVIDER` and `AGENT4_SIMULATE=false` for live delivery. Accounts come from `AGENT4_ACCOUNTS_FILE` or `SENDER_EMAIL`.
- Stores sends, tracking events, sequence states, accounts, suppressions, and jobs in `agents/agent4-sender/output/sends.db`.
- A reply handoff is a JSON file at `agents/agent4-sender/output/replies/reply_<lead-id>.json`. Agent 5 writes stop-sequence signals to the sibling `output/signals/` directory.

### Agent 5 — Reply Handler

Entrypoints: `ReplyHandlerAgent.run(job_id=None)` drains Agent 4 handoffs; `handle_payload(payload)` handles a single webhook payload. Both are in [agents/agent5-reply-handler/agent.py](agents/agent5-reply-handler/agent.py).

- Input layer reads/parses reply files and loads original email/conversation context.
- Understanding layer determines intent, sentiment, urgency, and decision confidence.
- Action layer chooses auto-reply, meeting brokering, objection handling, or human handoff.
- Output layer persists conversation state, sends/drafts a response, emits notification JSON, and stops the sequence when appropriate.
- The automatic-handling floor is 0.70 confidence. Pricing/legal/etc., negative sentiment, high-value leads (score > 8), or more than three exchanges are escalation paths.
- Defaults to simulation. Live outbound response support is SMTP or Gmail. It writes `conversations.db` plus notification artifacts under `agents/agent5-reply-handler/output/`.

## Data and handoff boundaries

| Producer | Consumer | Contract/location |
| --- | --- | --- |
| Agent 1 | Agent 2 and orchestrator | lead artifacts; live orchestrator ingests `output/leads_*.jsonl` |
| Agent 2 | Agent 3 and orchestrator | research artifacts; live orchestrator reads `output/research_*.jsonl` |
| Agent 3 | Agent 4 and orchestrator | `output/emails.db`, `emails` table (`lead_id`, `status`, `quality_score`) |
| Agent 4 | Agent 5 and orchestrator | `output/sends.db` plus reply-handoff JSON files |
| Agent 5 | Agent 4 and orchestrator | `conversations.db`, handoff notifications, stop-sequence signals |
| Orchestrator | all stages | master state/event/run records in `orchestrator.db` |

The live adapter intentionally reconciles outputs defensively: missing artifacts can cause a stage to appear advanced instead of crashing the full cycle. Validate artifacts explicitly before relying on live campaign results.

## Environment and external services

Never commit `.env`: it and `.env.*` are ignored. Relevant variables include:

| Area | Variables |
| --- | --- |
| LLMs | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` |
| Lead finding | `GOOGLE_PLACES_API_KEY`, `TAVILY_API_KEY`, `HUNTER_API_KEY`, `ABSTRACT_API_KEY`, `WAPPALYZER_API_KEY`, `CRUNCHBASE_API_KEY`, `WHOISXML_API_KEY`, `SECURITYTRAILS_API_KEY`, `REDIS_URL` |
| Research | `FIRECRAWL_API_KEY`, `GNEWS_API_KEY`, `GITHUB_API_KEY` plus `TAVILY_API_KEY` / `WAPPALYZER_API_KEY` |
| Sender identity | `SENDER_EMAIL`, `SENDER_FROM_NAME`, `SENDER_FIRST_NAME`, `SENDER_LAST_NAME`, `SENDER_TITLE`, `SENDER_COMPANY`, `SENDER_SIGNATURE`, `SENDER_LINKEDIN_URL`, `SENDER_PHONE` |
| Agent 4 mode/accounts | `AGENT4_PROVIDER`, `AGENT4_SIMULATE`, `AGENT4_ACCOUNTS_FILE` |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` |
| Gmail | `GMAIL_ACCESS_TOKEN` |
| Other sending providers | `INSTANTLY_API_KEY`, `OUTREACH_ACCESS_TOKEN`, `AWS_SES_REGION` or `AWS_REGION` / `AWS_DEFAULT_REGION` |
| Agent 5 | `AGENT5_SIMULATE`, `CALENDLY_LINK` |
| Backend | `AUTOREACH_ENV`, `AUTOREACH_API_SECRET`, `AUTOREACH_DATA_DIR`, `AUTOREACH_CORS_ORIGINS`, `AUTOREACH_SCHEDULER_ENABLED`, `AUTOREACH_SCHEDULER_TIMEZONE`, `AUTOREACH_SIMULATE`, `AUTOREACH_REPLY_HANDLING_ENABLED` |

Install the declared dependencies before importing the live agents:

```bash
python -m pip install -r requirements.txt
```

`boto3` is required for AWS SES; `redis[asyncio]` is required by Agent 1; `firecrawl-py` is required by Agent 2. LLM and provider calls require valid credentials and are deliberately not safe to run casually.

## Working safely

1. Start with `OrchestratorConfig(simulate=True)`; Agents 4 and 5 independently also default to simulation.
2. Do not turn off simulation or run a live sender until account setup, sender identity, unsubscribe/compliance process, recipient consent/legitimate-interest policy, tracking endpoint, and reputation safeguards have been reviewed.
3. Keep lead IDs stable. They are the cross-agent correlation key and are used for reconciliation.
4. Treat `output/` DBs and JSON files as runtime state. The existing JSON examples are sample/generated artifacts, not evidence of a real active campaign.
5. Before modifying flow behavior, trace both the agent’s persistence output and `orchestrator/adapters/live.py`; the adapter’s expected fields and file names are an integration contract.

## Known gaps and things to verify before production

- The backend is intentionally a single-instance MVP. It has no migration system or CI workflow, and its API worker/scheduler cannot scale horizontally while SQLite/file contracts remain.
- `POST /v1/events/sender` accepts an authenticated normalized event, not a raw public provider webhook. SES/Gmail/Instantly adapters must validate provider signatures before forwarding events.
- The default orchestrator is simulated. A live campaign is not enabled simply by adding keys; it must use `simulate=False`, running Redis, compatible artifacts, sender accounts, and provider credentials.
- Agent 4/5 reply detection and tracking are exposed as methods/files, but no inbound webhook server, Gmail polling service, or tracking HTTP endpoint is included in this repo.
- Agent 4 follow-up reconciliation currently reports all selected leads as `continued`; verify sequence exhaustion/closure behavior when wiring a production scheduler.
- Agent 5 live-adapter reconciliation looks up conversation rows by `id` using the pipeline lead ID. Confirm that the stored conversation schema uses the same identifier before relying on meeting/escalation state reconciliation.
- `OrchestratorConfig.from_env()` currently returns defaults only; most campaign settings are code defaults rather than environment-configurable.
- `output/` data is not ignored by the present `.gitignore` (only env files and Python cache are). Avoid committing runtime DBs or real lead/reply artifacts; consider expanding ignore rules in a dedicated change.
- The local `.env` loader is intentionally simple: it supports plain `KEY=VALUE` lines only and does not replace existing environment values.

## Recommended first reads for a future agent

1. [10-full-system-architecture.md](10-full-system-architecture.md) for product intent and policy assumptions.
2. [orchestrator/orchestrator.py](orchestrator/orchestrator.py), [orchestrator/state_machine.py](orchestrator/state_machine.py), and [orchestrator/config.py](orchestrator/config.py) for the runtime control flow.
3. The target agent’s `agent.py`, `config.py`, `models.py`, and `storage/` directory.
4. [orchestrator/adapters/live.py](orchestrator/adapters/live.py) before changing any cross-agent output schema.
5. [core/model_selection/__init__.py](core/model_selection/__init__.py) and provider adapters before changing LLM/model behavior.
