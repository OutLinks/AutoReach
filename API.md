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
      "redis_url": "redis://redis:6379/0",
      "scheduler_timezone": "UTC"
    }
  }'
```

Settings and credentials are written through `PATCH /v1/settings`; they do not
need to be placed in a frontend environment file.

Live email writing requires `sender_first_name`, `sender_last_name`, and
`sender_email`. Optional sender identity settings include `sender_title`,
`sender_company`, `sender_signature`, `sender_linkedin_url`, and `sender_phone`.

Live lead discovery requires at least one discovery source:

- configure `tavily_api_key` for public web search;
- configure one or more public pages in `lead_finder_source_urls`; or
- include a public company/directory URL in the search query.

The `tavily_enabled` toggle is database-backed and defaults to enabled, but web
search runs only when its key is configured. MVP lead discovery does not use
Apollo, Google Places, or other structured lead databases; those services are
outside the web-search and web-scraping discovery path.

Web-search results are candidate URLs, not leads. Each candidate must be scraped
successfully and expose a company-domain email before it is returned. MVP lead
records include `company_name`, `email`, `company_description`,
`company_website`, and the public URLs used as evidence; records without a
public company-domain email are omitted.

Completed search results are also persisted to the canonical lead store as
`new` search previews. They can be researched immediately by lead ID, but do
not enter the outreach pipeline until
`POST /v1/lead-finding/searches/{search_id}/import` promotes them to
`discovered`. When a selected lead has no enrichment, live research enables the
configured Tavily provider and performs a public-site scrape (without requiring
Firecrawl) before analysis.

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
