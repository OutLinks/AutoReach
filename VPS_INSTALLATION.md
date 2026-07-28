# AutoReach API on an Ubuntu VPS

This deployment runs the API privately on `127.0.0.1:8000`. The application has
no bundled frontend and currently performs no authentication.

> Do not publish this version directly to the internet. Anyone who can reach it
> can change provider credentials, start agents, and trigger live sending.

## 1. Create the deployment directory

```bash
sudo mkdir -p /opt/autoreach
sudo chown "$USER":"$USER" /opt/autoreach
cd /opt/autoreach
```

Copy the repository's `compose.yaml` to this directory, or create it with:

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  autoreach:
    image: janudax/autoreach:latest
    pull_policy: always
    restart: unless-stopped
    environment:
      AUTOREACH_ENV: production
      AUTOREACH_DATA_DIR: /data
      AUTOREACH_SCHEDULER_INTERVAL_SECONDS: "30"
    depends_on:
      redis:
        condition: service_healthy
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - autoreach_data:/data
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
        ]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s

volumes:
  autoreach_data:
  redis_data:
```

Provider credentials, sender identity, Redis URL, and other operational
configuration are not stored in this file. They are stored in the selected
SQLite database and managed through the API.

## 2. Pull and start

```bash
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=100 autoreach
```

Verify from the VPS:

```bash
curl http://127.0.0.1:8000/healthz
```

## 3. Access the API privately

From your computer:

```bash
ssh -L 8000:127.0.0.1:8000 YOUR_USER@YOUR_VPS_IP
```

Use:

```text
http://localhost:8000/docs
http://localhost:8000/openapi.json
```

## 4. Select the database

```bash
curl -sS -X POST http://localhost:8000/v1/setup \
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

Configure providers using `PATCH /v1/settings`. The values are stored in the
selected SQLite database, not in the environment file.

For an existing installation, select the Compose Redis service and configure the
sender identity through the API:

```bash
curl -sS -X PATCH http://localhost:8000/v1/settings \
  -H "Content-Type: application/json" \
  -d '{
    "values": {
      "redis_url": "redis://redis:6379/0",
      "sender_first_name": "Jane",
      "sender_last_name": "Doe",
      "sender_title": "Founder",
      "sender_company": "Example Company",
      "sender_email": "jane@example.com",
      "sender_signature": "Jane Doe\nFounder, Example Company"
    }
  }'
```

## 5. Control agents and the orchestrator

```bash
curl -sS http://localhost:8000/v1/agents

curl -sS -X POST \
  http://localhost:8000/v1/agents/agent1-lead-finder/run \
  -H "Content-Type: application/json" \
  -d '{}'

curl -sS -X POST \
  http://localhost:8000/v1/orchestrator/cycle
```

Poll returned jobs with:

```bash
curl -sS http://localhost:8000/v1/jobs/JOB_ID
```

## 6. Public integration

If a hosted frontend or another internet service must call this API, add an
authenticated gateway first. Suitable boundaries include Cloudflare Access, a
VPN, an API gateway, or Nginx authentication. Terminate HTTPS at that boundary
and continue proxying privately to `127.0.0.1:8000`.

Do not open host port `8000` publicly.

## 7. Updates and lifecycle

```bash
cd /opt/autoreach
sudo docker compose pull
sudo docker compose up -d
sudo docker compose logs --tail=100 autoreach
```

Stop without deleting volumes:

```bash
sudo docker compose down
```

Never add `-v` unless you intentionally want to delete the SQLite and Redis
volumes.
