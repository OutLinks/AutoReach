# AutoReach backend

AutoReach now exposes its orchestrator through an authenticated FastAPI service.
The service uses a durable SQLite job queue and runs jobs sequentially because the
current agents coordinate through local SQLite databases and JSON files.

## Local start

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
set -a; source .env; set +a
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/docs`. If `AUTOREACH_API_SECRET` is set, authorize
with that value as a Bearer token. `GET /healthz` is intentionally public for
hosting health checks; all `/v1` routes require the token in production.

In Conductor, the shared setup installs `.venv`; the **API** run command uses the
workspace's allocated port. Shared settings become available to all workspaces
after `.conductor/settings.toml` is merged into the repository's default branch.

## Basic API flow

```bash
export AUTOREACH_URL=http://127.0.0.1:8000
export AUTOREACH_TOKEN=replace-with-a-long-random-value

curl -sS -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Find property-management companies in Colombo for a simulated campaign."}' \
  "$AUTOREACH_URL/v1/campaigns"

curl -sS -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/jobs/JOB_ID"

curl -sS -X POST -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/campaigns/CAMPAIGN_ID/activate"

curl -sS -X POST -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/jobs/find"
```

Campaign planning uses the configured LLM even when pipeline simulation is on,
so it requires the corresponding model-provider key. Pipeline jobs return HTTP
202 and a durable job record. Poll `GET /v1/jobs/{id}` until it reaches
`succeeded` or `failed`.

## Render deployment

1. Push the branch and merge it into `main`.
2. In Render, create a Blueprint from this repository's `render.yaml`.
3. Supply `ANTHROPIC_API_KEY` when prompted. It is used by campaign planning.
4. After the first deploy, copy the generated `AUTOREACH_API_SECRET` from the
   service environment and keep it in your frontend/server secret store.
5. Confirm `/healthz`, authorize in `/docs`, and run a simulated find job.

The Blueprint creates one paid web service, a 1 GB persistent disk, and a
Redis-compatible Key Value service. Keep exactly one web instance and one
Uvicorn worker while SQLite/file storage is in use.

## Environment controls

| Variable | Purpose | Safe default |
| --- | --- | --- |
| `AUTOREACH_API_SECRET` | Bearer token; minimum 24 characters in production | required |
| `AUTOREACH_DATA_DIR` | Persistent root for API/orchestrator/agent state | repository-local |
| `AUTOREACH_SIMULATE` | Select simulated or live orchestrator adapters | `true` |
| `AUTOREACH_SCHEDULER_ENABLED` | Enqueue scheduled pipeline ticks | `false` |
| `AUTOREACH_SCHEDULER_TIMEZONE` | IANA timezone used by the daily schedule | `UTC` |
| `AUTOREACH_CORS_ORIGINS` | Comma-separated browser origins | none |
| `AUTOREACH_REPLY_HANDLING_ENABLED` | Enable Agent 5 orchestration | `false` |

## Webhook boundary

`POST /v1/events/sender` accepts normalized reply, bounce, complaint, open, and
click events using the API Bearer token. Do not point an email provider directly
at this endpoint. Add a provider adapter that validates the provider's signature,
normalizes the event, and then invokes this endpoint or the same executor.

## Production limits

This is a single-instance MVP. Before horizontal scaling, migrate coordination,
agent databases, and the job queue to PostgreSQL; move artifacts to object
storage; and run API, scheduler, and workers as separate services. Keep live
sending disabled until suppression/unsubscribe handling, webhook verification,
sender-domain authentication, and reputation monitoring have been reviewed.
