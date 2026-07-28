# AutoReach API deployment

AutoReach runs as an API-only FastAPI service. No frontend files are bundled or
served. Authentication is intentionally disabled in this version.

> Keep the service on a private network or behind an access-control layer.
> Configuration endpoints and live agent controls are currently open to any
> caller that can reach the API.

## Local start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Useful URLs:

```text
http://127.0.0.1:8000/healthz
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

`GET /` returns JSON service metadata rather than HTML.

## Select the database

The application starts with a default database under `AUTOREACH_DATA_DIR`.
Select the persistent database explicitly through the API:

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

Provider credentials and application settings are stored in that SQLite
database through `PATCH /v1/settings`. Secret values are write-only and are
masked by `GET /v1/settings`.

## Run agents

List available agents:

```bash
curl -sS http://127.0.0.1:8000/v1/agents
```

Run Agent 1:

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/v1/agents/agent1-lead-finder/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

Run Agent 4's sending stage:

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/v1/agents/agent4-sender/run \
  -H "Content-Type: application/json" \
  -d '{"stage":"send"}'
```

Run one orchestrator cycle:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/orchestrator/cycle
```

Execution calls return HTTP `202` and a durable job. Poll the returned `id`:

```bash
curl -sS http://127.0.0.1:8000/v1/jobs/JOB_ID
```

See [API.md](API.md) for all endpoints.

## Continuous deployment

GitHub Actions runs tests, publishes a multi-architecture Docker image, and
deploys successful `main` builds to the VPS. See [CICD.md](CICD.md) for the
required GitHub secrets, deployment key, and one-time Compose configuration.

## Docker Compose

```bash
docker compose pull
docker compose up -d
docker compose ps
```

The repository's `compose.yaml` runs the API and its Redis working queue. Redis
is internal to the Compose network; only the API is bound on
`127.0.0.1:8000`. Set `redis_url` to `redis://redis:6379/0` through
`POST /v1/setup` or `PATCH /v1/settings`.

Keep one Uvicorn worker and one service instance while SQLite is used.

## Production boundary

This unauthenticated build is intended for API development. Before public
deployment, place an authenticated gateway, VPN, Cloudflare Access, or another
trusted access-control layer in front of it. Keep simulation enabled until
provider credentials, suppression handling, sender-domain authentication,
webhook verification, and reputation monitoring are ready.
