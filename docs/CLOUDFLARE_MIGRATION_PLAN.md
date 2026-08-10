# Cloudflare migration inventory and staged plan

This inventory was captured before the durable-storage migration. It is the
cutover checklist for the existing Python application; it is not an assertion
that the current local adapters are safe in a Cloudflare Container.

## Affected persistence and runtime boundaries

| Boundary | Current source of truth | Production replacement | Status |
| --- | --- | --- | --- |
| API jobs | `api/jobs.py`, SQLite plus in-process queue | D1 `jobs`, `job_attempts`, `job_outbox`; Queues | Implemented adapter, not enabled globally |
| Scheduler | `api/scheduler.py` loop | Worker Cron, Queue, Workflow sleeps | Implemented for durable jobs |
| App settings | `api/config_store.py`, SQLite | D1 `app_settings`; Worker Secrets for credentials | Secrets protection implemented; D1 adapter pending |
| Orchestration | `orchestrator/store.py`, SQLite | D1 campaigns, leads, events | Schema present; adapter pending |
| Interactive workflows | `api/workflows.py`, SQLite | D1 research/drafts/search tables; R2 bodies where large | Schema present; adapter pending |
| Agent 1 pipeline | Redis lists/hashes/TTLs and JSONL | D1 temporary stage records, canonical `leads`, unique dedupe hashes | Implemented |
| Agent 2 research | JSONL | R2 JSON objects with D1 artifact index | Implemented in container mode |
| Agent 3 inputs/output | JSONL/files and SQLite | D1/R2 repository readers and D1 written-email records | Implemented in container mode |
| Agent 4 sender | SQLite emails/sends | D1 send/sequence/event records | D1 adapter and Agent 3 D1 reader implemented; Workflow scheduling pending |
| Agent 5 replies | SQLite and reply files | D1 conversations/reply events; R2 raw payloads | Schema present; adapter pending |

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
state. Email sends additionally require a durable sender idempotency key and
provider message ID before Agent 4 can be moved to this path.

## Redis decision

Redis is not replaced with Queues. Agent 1's job state, stage lists, TTLs, and
global deduplication are represented by D1 `lead_pipeline_jobs`,
`lead_stage_records`, and `lead_dedup_keys`; completed nonduplicate leads are
also upserted into the canonical D1 `leads` table. Stage expiry is enforced on
reads, but downstream agents read `leads`, not temporary stage data. Dedupe
values are SHA-256 hashes. This preserves the data semantics while retaining
Queues only for durable work dispatch.

## Cutover stages

1. Deploy D1/R2/Queue resources and apply all D1 migrations without enabling
   `AUTOREACH_STORAGE_BACKEND=d1`.
2. Move orchestrator and workflow repositories to their D1 interfaces. Agent 2
   now indexes its R2 profiles in D1; Agent 3 selects that index, fetches the
   R2 body, and reads the matching Agent 1 scored lead through D1.
3. Register delayed follow-up Workflows from Agent 4 sequence state, then move
   Agent 5 to D1. Verify idempotent sends, provider event uniqueness, and
   delayed follow-up Workflow creation.
4. Add an app-settings D1 repository and prohibit all Cloudflare-container
   paths from opening local SQLite or JSONL artifacts.
5. Backfill data, validate counts/checksums and idempotency keys, then enable
   external execution with `AUTOREACH_STORAGE_BACKEND=d1`.
6. Keep Compose stopped-but-restorable through the first verified production
   campaign. Roll back by disabling Worker routes and Queue dispatch; never run
   the old and new writers for a campaign concurrently.
