# Running autoreach with Docker

Everything runs from **one image**. The orchestrator is the CEO process: it owns
the state machine and drives all 5 agents in-process. The per-agent services
exist for running/debugging a single stage on its own.

## 1. Configure secrets

```bash
cp .env.example .env      # then fill in API keys (Anthropic, Supabase, etc.)
```

`docker-compose.yml` loads `.env` at runtime — secrets are never baked into the image.

By default the orchestrator runs in **simulate mode** (no real API calls). To run
the real agents, set in `.env`:

```
ORCH_SIMULATE=0
```

## 2. Run the whole system

```bash
docker compose up --build orchestrator
```

This starts Redis (the Agent 1 message bus) and the orchestrator, which cycles
the pipeline forever. Tuning knobs (env vars):

| var            | default | meaning                                        |
|----------------|---------|------------------------------------------------|
| `ORCH_SIMULATE`| `1`     | `0` = run real agents/APIs                      |
| `ORCH_MODE`    | `loop`  | `once` = single pass then exit                 |
| `ORCH_INTERVAL`| `300`   | seconds between cycles in loop mode            |

## 3. Run a single agent (debug)

```bash
docker compose run --rm agent1 "find SaaS founders in NYC"
docker compose run --rm agent2          # uses AGENT_LEAD_IDS env if set
docker compose --profile agents up      # bring up all agent services at once
```

Agents share the same output volumes as the orchestrator, so the JSONL hand-off
files are consistent across both run styles.

## 4. Deploy on a server

```bash
git clone <repo> && cd <repo>
cp .env.example .env && $EDITOR .env
docker compose up -d --build orchestrator   # detached
docker compose logs -f orchestrator
```

State persists in named volumes (`orchout`, `a1out`…`a5out`, `redisdata`).
Supabase is a hosted service — point at it via `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`
in `.env`; no container needed.
