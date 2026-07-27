# AutoReach API

AutoReach is an API-only FastAPI service. It does not bundle a frontend and
authentication is intentionally disabled at this stage.

> Do not expose this version to an untrusted network. Every endpoint, including
> configuration writes and live sending controls, is currently unauthenticated.

## Service endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | API metadata and links |
| `GET` | `/healthz` | Process health check |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/redoc` | ReDoc |
| `GET` | `/openapi.json` | OpenAPI schema |

## Database and settings

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/setup` | Report database-selection status |
| `POST` | `/v1/setup` | Select a persistent SQLite database and apply initial settings |
| `GET` | `/v1/settings` | List database-backed settings with secret values masked |
| `PATCH` | `/v1/settings` | Update provider, sender, scheduler, and pipeline settings |
| `GET` | `/v1/config` | Read the effective non-secret runtime configuration |

Select the bundled Docker volume database with:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/setup \
  -H "Content-Type: application/json" \
  -d '{
    "database_path": "/data/autoreach.db",
    "settings": {
      "simulate": true,
      "scheduler_timezone": "UTC"
    }
  }'
```

Settings and credentials are written through `PATCH /v1/settings`; they do not
need to be placed in a frontend environment file.

## Agent control

`GET /v1/agents` returns the available agents and their stages.

Run an agent that owns one stage:

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/v1/agents/agent1-lead-finder/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

Agent 4 owns both `send` and `followup`, so choose the stage:

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/v1/agents/agent4-sender/run \
  -H "Content-Type: application/json" \
  -d '{"stage":"send"}'
```

The stage-oriented compatibility endpoint remains available:

```text
POST /v1/jobs/stages/{stage_name}
```

Valid stages are `find`, `research`, `write`, `send`, `followup`, and `reply`.
All execution endpoints return HTTP `202` with a durable job record.

## Orchestrator control

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/orchestrator/cycle` | Queue one orchestrator pipeline cycle |
| `GET` | `/v1/orchestrator/health` | Read orchestrator health |
| `GET` | `/v1/orchestrator/report` | Generate the current pipeline report |
| `POST` | `/v1/jobs/find` | Queue the lead-finding stage |
| `POST` | `/v1/jobs/cycle` | Compatibility endpoint for a pipeline cycle |
| `GET` | `/v1/jobs` | List durable jobs |
| `GET` | `/v1/jobs/{job_id}` | Poll one durable job |

## Campaigns, leads, and events

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/campaigns` | Queue campaign planning from a prompt |
| `GET` | `/v1/campaigns` | List campaigns |
| `GET` | `/v1/campaigns/{campaign_id}` | Read a campaign |
| `POST` | `/v1/campaigns/{campaign_id}/activate` | Activate a campaign |
| `GET` | `/v1/leads` | List and filter leads |
| `GET` | `/v1/health` | Compatibility endpoint for orchestrator health |
| `GET` | `/v1/report` | Compatibility endpoint for the pipeline report |
| `POST` | `/v1/events/sender` | Queue a normalized sender event |

Provider-specific webhook signatures must be validated before forwarding a
normalized event to `/v1/events/sender`.
