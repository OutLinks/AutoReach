# ADR 0001: Cloudflare Containers execution architecture

## Status

Accepted and implemented. Local SQLite, Redis, JSONL, and filesystem handoffs
remain development-only adapters.

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
stripped at the edge. Sender records use durable idempotency keys, D1 capacity
reservations, and provider message IDs around every send attempt. A reserved
ambiguous delivery is never automatically resent. Provider events are
deduplicated by stable provider event IDs. Agent 5 claims inbound events and
reserves deterministic outbound replies before provider I/O.

## Consequences

The job queue, settings, orchestrator, interactive workflows, campaigns, leads,
mailbox, Agent 1 pipeline/dedupe, Agent 2 R2 profile index, Agent 3 written
emails, Agent 4 sender/sequence/tracking state, and Agent 5 conversations/reply
events all have named D1/R2 production repositories. Cron produces
timezone-aware durable tick jobs, and Cloudflare Workflows own multi-day
follow-up sleeps. Production uses one container instance initially; increasing
concurrency requires a separate capacity and provider-safety review.
