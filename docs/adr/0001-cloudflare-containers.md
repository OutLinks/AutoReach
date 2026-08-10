# ADR 0001: Cloudflare Containers execution architecture

## Status

Accepted for incremental implementation; local SQLite/filesystem storage remains
development-only until the D1/R2 persistence cutover is complete.

## Context

AutoReach is a Python/FastAPI application with five Python agents. It currently
uses local SQLite databases, JSON/JSONL artifacts, Redis for Agent 1 pipeline
state, an in-memory job queue, and an in-process scheduler. A sleeping or
restarted Cloudflare Container cannot use that local state as a production
source of truth.

## Decision

- Retain Python/FastAPI and all provider clients inside a Linux/amd64 Cloudflare
  Container.
- Route all public traffic through an authenticated TypeScript Worker to one
  stable `autoreach-primary` Container-backed Durable Object. The initial
  maximum number of running instances is one.
- Use D1 as the canonical relational store and R2 for versioned artifact
  bodies. Durable Object storage is only for container control-plane concerns.
- Use Queues for `{job_id}` dispatch/retry and Workflows for follow-up sleeps
  beyond Queues' 24-hour delay limit.
- Replace Agent 1 Redis lists, TTL state, and global dedupe sets with D1 stage
  records, expiry cleanup, and unique dedupe keys.
- Keep SMTP and existing HTTPS sending providers in the Python container.
- Keep credentials in Worker Secrets/Secret Store. The Worker injects only the
  required values into the container. Production API settings must not write
  plaintext secrets into application storage.

## Job correctness model

Queues are at-least-once. A D1 job row is conditionally claimed with a lease
token and expiry before Python executes it; terminal updates require the same
token. A Queue message contains only the job ID. Cron requeues expired claims
through the transactional outbox, which makes a container loss recoverable
without reclaiming a live attempt. The internal execution endpoint requires an
HMAC signature created by the Worker; user-provided internal headers are
stripped at the edge. Sender records will use durable idempotency keys and
provider message IDs before any send attempt.

## Consequences

The Worker wrapper, D1-backed job queue, Agent 1 D1 pipeline adapter, Agent 2
R2 writer plus D1 profile index, Agent 3 D1 written-email repository, and Agent
4 D1 sender-state adapter are implemented. It does not make the remaining
SQLite workflow/orchestrator, delayed sequence registration, or Agent 5
repositories production-safe. Their persistence and handoff cutover is a
separate, tested phase before Cloudflare deployment.
