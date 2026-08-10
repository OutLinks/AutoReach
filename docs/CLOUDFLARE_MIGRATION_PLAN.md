# Cloudflare migration inventory and staged plan

This inventory was captured before the migration and now records the completed
production cutover. Local adapters are retained only for development.

## Affected persistence and runtime boundaries

| Boundary | Current source of truth | Production replacement | Status |
| --- | --- | --- | --- |
| API jobs | `api/jobs.py`, SQLite plus in-process queue | D1 `jobs`, `job_attempts`, `job_outbox`; Queues | Implemented and selected in container mode |
| Scheduler | `api/scheduler.py` loop | Worker Cron, Queue, Workflow sleeps | Implemented; local loop disabled in container mode |
| App settings | `api/config_store.py`, SQLite | D1 `app_settings`; Worker Secrets for credentials | Implemented |
| Orchestration | `orchestrator/store.py`, SQLite | D1 campaigns, leads, events, runs, dead letters | Implemented |
| Interactive workflows | `api/workflows.py`, SQLite | D1 research/drafts/search/mailbox/operator tables; R2 bodies where large | Implemented |
| Agent 1 pipeline | Redis lists/hashes/TTLs and JSONL | D1 temporary stage records, canonical `leads`, unique dedupe hashes | Implemented |
| Agent 2 research | JSONL | R2 JSON objects with D1 artifact index | Implemented in container mode |
| Agent 3 inputs/output | JSONL/files and SQLite | D1/R2 repository readers and D1 written-email records | Implemented in container mode |
| Agent 4 sender | SQLite emails/sends | D1 send/sequence/event/reservation records; Workflows | Implemented |
| Agent 5 replies | SQLite and reply files | D1 conversations, claimed inbound events, reserved outbound replies | Implemented |

`api/executor.py` retains `asyncio.Queue` and `asyncio.create_task` only in
explicit `AUTOREACH_EXECUTOR_MODE=local`. Cloudflare container mode disables the
local executor and scheduler. Dynamic agent loading remains in
`orchestrator/adapters/live.py`; the Worker deliberately does not replace the
Python agent runtime.

## Durable job lifecycle

```text
API request -> D1 jobs + job_outbox (one transaction)
            -> Queue message { job_id }
            -> signed Worker-to-container /internal/jobs/:id/execute
            -> conditional D1 lease claim -> Python execution
            -> token-checked terminal update

Container loss -> lease expiry -> Cron restores outbox -> Queue retry
```

Jobs use an at-least-once Queue delivery model. The conditional lease and
token-checked completion prevent two attempts from both reaching a terminal
state. Email sends additionally use deterministic durable idempotency keys. D1
atomically reserves account/day/hour/burst/domain capacity before provider I/O;
ambiguous reservations require reconciliation and are not automatically resent.

## Redis decision

Redis is not replaced with Queues. Agent 1's job state, stage lists, TTLs, and
global deduplication are represented by D1 `lead_pipeline_jobs`,
`lead_stage_records`, and `lead_dedup_keys`; completed nonduplicate leads are
also upserted into the canonical D1 `leads` table. Stage expiry is enforced on
reads, but downstream agents read `leads`, not temporary stage data. Dedupe
values are SHA-256 hashes. This preserves the data semantics while retaining
Queues only for durable work dispatch.

## Completed cutover stages

Stages 1–4 are implemented in code and covered by production contract tests.
Deployment-specific resource creation, data backfill, live provider validation,
and the first simulated/live canaries remain operator release steps; follow the
checklist in `CLOUDFLARE_DEPLOYMENT.md`.
