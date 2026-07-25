# AutoReach VPS Installation Guide

This guide installs AutoReach on an Ubuntu VPS using the published Docker image:

```text
janudax/autoreach:latest
```

The image supports both `linux/amd64` and `linux/arm64`.

## Requirements

- Ubuntu 22.04 or 24.04
- At least 2 vCPU, 4 GB RAM, and 40 GB SSD recommended
- A user with `sudo` access
- Anthropic API key for campaign planning
- Optional email and research-provider API keys
- A domain name if the API will be exposed publicly

## 1. Connect to the VPS

From your local computer:

```bash
ssh YOUR_USER@YOUR_VPS_IP
```

Update the server:

```bash
sudo apt update
sudo apt upgrade -y
```

## 2. Install Docker

If Docker is already installed, verify it and skip to the next section:

```bash
docker --version
docker compose version
```

Otherwise, install Docker Engine and the Docker Compose plugin:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings

sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc

sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

Official references:

- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Install the Docker Compose plugin](https://docs.docker.com/compose/install/linux/)

## 3. Create the deployment directory

```bash
sudo mkdir -p /opt/autoreach
sudo chown "$USER":"$USER" /opt/autoreach
cd /opt/autoreach
```

The completed directory will contain:

```text
/opt/autoreach/
├── .env
└── compose.yaml
```

Application databases are stored in Docker-managed volumes, not in this
directory.

## 4. Generate the API secret

Generate a strong administrative API secret:

```bash
openssl rand -hex 32
```

Copy the generated value. It will be used as `AUTOREACH_API_SECRET`.

This is an administrative service secret. Do not place it in a public frontend
bundle or commit it to Git.

## 5. Create the environment file

Create the file:

```bash
nano /opt/autoreach/.env
```

Paste the following configuration and replace the placeholder values:

```env
# Core API
AUTOREACH_ENV=production
AUTOREACH_API_SECRET=PASTE_GENERATED_SECRET_HERE
AUTOREACH_DATA_DIR=/data

# Start safely in simulation mode
AUTOREACH_SIMULATE=true
AUTOREACH_REPLY_HANDLING_ENABLED=false
AUTOREACH_SCHEDULER_ENABLED=false
AUTOREACH_SCHEDULER_INTERVAL_SECONDS=30
AUTOREACH_SCHEDULER_TIMEZONE=Asia/Colombo

# Add the frontend origins that are allowed to call the API
AUTOREACH_CORS_ORIGINS=http://localhost:3000

# Pipeline limits
AUTOREACH_FOLLOWUP_AFTER_DAYS=3
AUTOREACH_LEADS_PER_DAY=50
AUTOREACH_EMAILS_PER_DAY=50
AUTOREACH_FOLLOWUPS_PER_DAY=100
AUTOREACH_DAILY_SEND_LIMIT=50
AUTOREACH_HOURLY_SEND_LIMIT=10

# Campaign planning model
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY
OPENAI_API_KEY=
OPENROUTER_API_KEY=

# Research providers
FIRECRAWL_API_KEY=
GOOGLE_PLACES_API_KEY=
TAVILY_API_KEY=
HUNTER_API_KEY=
ABSTRACT_API_KEY=

# Email delivery remains simulated initially
AGENT4_PROVIDER=resend
AGENT4_SIMULATE=true
AGENT4_REPLY_HANDOFF_ENABLED=false
AGENT4_TRACKING_BASE_URL=https://api.example.com

AGENT5_ENABLED=false
AGENT5_SIMULATE=true

SENDER_EMAIL=
SENDER_FROM_NAME=AutoReach
AGENT4_ACCOUNTS_FILE=

# Resend
RESEND_API_KEY=

# SendGrid
SENDGRID_API_KEY=
SENDGRID_API_BASE=https://api.sendgrid.com

# Mailgun
MAILGUN_API_KEY=
MAILGUN_DOMAIN=
MAILGUN_API_BASE=https://api.mailgun.net/v3

# Postmark
POSTMARK_SERVER_TOKEN=
POSTMARK_MESSAGE_STREAM=outbound

# Gmail, Instantly, and Outreach
GMAIL_ACCESS_TOKEN=
INSTANTLY_API_KEY=
OUTREACH_ACCESS_TOKEN=

# AWS SES uses the standard AWS credential chain
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SES_REGION=

# Generic SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
```

Save the file in Nano with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

Restrict access to the file:

```bash
chmod 600 /opt/autoreach/.env
```

## 6. Create the Docker Compose file

Create:

```bash
nano /opt/autoreach/compose.yaml
```

Paste:

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
    env_file:
      - .env
    environment:
      REDIS_URL: redis://redis:6379/0
      AUTOREACH_DATA_DIR: /data
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

The named volumes preserve AutoReach SQLite databases and Redis data when
containers are upgraded or recreated.

## 7. Pull and start AutoReach

Change to the deployment directory:

```bash
cd /opt/autoreach
```

If the Docker Hub repository requires authentication:

```bash
sudo docker login
```

Pull the images:

```bash
sudo docker compose pull
```

Start the services:

```bash
sudo docker compose up -d
```

Check their status:

```bash
sudo docker compose ps
```

Both `redis` and `autoreach` should eventually show a healthy status.

View application logs:

```bash
sudo docker compose logs --tail=100 autoreach
```

Follow logs continuously:

```bash
sudo docker compose logs -f autoreach
```

Press `Ctrl+C` to stop following the logs. This does not stop the container.

## 8. Test through an SSH tunnel

Port `8000` is deliberately bound to the VPS loopback interface. This prevents
the unencrypted administrative API from being exposed directly to the internet.

On your local computer, create a tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 YOUR_USER@YOUR_VPS_IP
```

Leave that terminal open.

In another local terminal, test the public health endpoint:

```bash
curl http://localhost:8000/healthz
```

Expected response:

```json
{"status":"ok"}
```

Open the interactive API documentation:

```text
http://localhost:8000/docs
```

Test authentication:

```bash
curl \
  -H "Authorization: Bearer YOUR_AUTOREACH_API_SECRET" \
  http://localhost:8000/v1/config
```

## 9. Run a simulated pipeline job

On your local computer while the SSH tunnel is active:

```bash
export AUTOREACH_URL=http://localhost:8000
export AUTOREACH_TOKEN=YOUR_AUTOREACH_API_SECRET
```

Create a campaign draft:

```bash
curl -X POST \
  -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Find property-management companies in Colombo with 10 to 100 employees."
  }' \
  "$AUTOREACH_URL/v1/campaigns"
```

The API returns a durable job. Copy its `id` and poll it:

```bash
curl \
  -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/jobs/JOB_ID"
```

Wait until the status becomes `succeeded` or `failed`.

After reviewing the campaign returned in `result`, activate it:

```bash
curl -X POST \
  -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/campaigns/CAMPAIGN_ID/activate"
```

Run simulated lead discovery:

```bash
curl -X POST \
  -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/jobs/find"
```

List leads:

```bash
curl \
  -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  "$AUTOREACH_URL/v1/leads"
```

## 10. Enable a live email provider

Do this only after campaign creation and the simulated pipeline work correctly.

Edit:

```bash
nano /opt/autoreach/.env
```

### Resend example

```env
AUTOREACH_SIMULATE=false
AGENT4_SIMULATE=false

AGENT4_PROVIDER=resend
SENDER_EMAIL=outreach@yourdomain.com
SENDER_FROM_NAME=Your Company
RESEND_API_KEY=re_xxxxxxxxx
```

### SendGrid example

```env
AUTOREACH_SIMULATE=false
AGENT4_SIMULATE=false

AGENT4_PROVIDER=sendgrid
SENDER_EMAIL=outreach@yourdomain.com
SENDER_FROM_NAME=Your Company
SENDGRID_API_KEY=SG.xxxxxxxxx
```

### Mailgun example

```env
AUTOREACH_SIMULATE=false
AGENT4_SIMULATE=false

AGENT4_PROVIDER=mailgun
SENDER_EMAIL=outreach@yourdomain.com
SENDER_FROM_NAME=Your Company
MAILGUN_API_KEY=key-xxxxxxxxx
MAILGUN_DOMAIN=mg.yourdomain.com
```

### Postmark example

```env
AUTOREACH_SIMULATE=false
AGENT4_SIMULATE=false

AGENT4_PROVIDER=postmark
SENDER_EMAIL=outreach@yourdomain.com
SENDER_FROM_NAME=Your Company
POSTMARK_SERVER_TOKEN=xxxxxxxxx
POSTMARK_MESSAGE_STREAM=outbound
```

Verify the sender address or domain with the selected provider before sending.

Recreate the application container after editing `.env`:

```bash
cd /opt/autoreach
sudo docker compose up -d --force-recreate autoreach
sudo docker compose logs --tail=100 autoreach
```

Keep the scheduler disabled while testing live sending manually:

```env
AUTOREACH_SCHEDULER_ENABLED=false
```

After live sending is verified, enable automation:

```env
AUTOREACH_SCHEDULER_ENABLED=true
AUTOREACH_SCHEDULER_TIMEZONE=Asia/Colombo
```

Apply the change:

```bash
sudo docker compose up -d --force-recreate autoreach
```

## 11. Public HTTPS access

Do not change the Compose port mapping to `0.0.0.0:8000:8000` for production.
Keep AutoReach on:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

Place Caddy, Nginx, Traefik, or another reverse proxy in front of the API and
terminate HTTPS there.

Before configuring HTTPS:

1. Create a DNS `A` record such as `api.example.com`.
2. Point it to the VPS public IPv4 address.
3. Open ports `80` and `443`.
4. Proxy HTTPS traffic to `127.0.0.1:8000`.
5. Update `.env`:

```env
AUTOREACH_CORS_ORIGINS=https://app.example.com
AGENT4_TRACKING_BASE_URL=https://api.example.com
```

The administrative bearer token must never travel over plain public HTTP.

## 12. Updating AutoReach

The `latest` Docker tag points to the newest published image.

Update with:

```bash
cd /opt/autoreach
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=100 autoreach
```

After verifying the update:

```bash
sudo docker image prune -f
```

Docker volumes are not removed by these commands.

## 13. Stopping and restarting

Restart services:

```bash
cd /opt/autoreach
sudo docker compose restart
```

Stop services without deleting data:

```bash
sudo docker compose down
```

Start them again:

```bash
sudo docker compose up -d
```

Do not run `docker compose down --volumes` unless you intentionally want to
delete AutoReach and Redis data.

## 14. Backing up application data

List the volumes:

```bash
sudo docker volume ls | grep autoreach
```

Create a backup directory:

```bash
sudo mkdir -p /opt/autoreach/backups
```

Stop AutoReach briefly so the SQLite files are consistent:

```bash
cd /opt/autoreach
sudo docker compose stop autoreach
```

Back up the AutoReach data volume:

```bash
sudo docker run --rm \
  -v autoreach_autoreach_data:/source:ro \
  -v /opt/autoreach/backups:/backup \
  alpine \
  tar czf /backup/autoreach-data.tar.gz -C /source .
```

Restart AutoReach:

```bash
sudo docker compose start autoreach
```

The exact volume name is normally `autoreach_autoreach_data`. Confirm it using
`docker volume ls` before running the backup command.

Also back up configuration:

```bash
sudo cp /opt/autoreach/.env /opt/autoreach/backups/autoreach.env
sudo cp /opt/autoreach/compose.yaml /opt/autoreach/backups/compose.yaml
sudo chmod 600 /opt/autoreach/backups/autoreach.env
```

Store backups somewhere outside the VPS as well.

## 15. Troubleshooting

### Container does not start

```bash
cd /opt/autoreach
sudo docker compose ps
sudo docker compose logs --tail=200 autoreach
```

### Production secret error

`AUTOREACH_API_SECRET` must contain at least 24 characters:

```bash
openssl rand -hex 32
```

Update `.env`, then recreate the container.

### Campaign creation fails

Campaign planning requires a valid model-provider key even when pipeline
simulation is enabled:

```env
ANTHROPIC_API_KEY=YOUR_KEY
```

Then:

```bash
sudo docker compose up -d --force-recreate autoreach
```

### Redis errors

```bash
sudo docker compose ps redis
sudo docker compose logs --tail=100 redis
sudo docker compose exec redis redis-cli ping
```

Expected:

```text
PONG
```

### API is unavailable through the SSH tunnel

Check that AutoReach is listening:

```bash
sudo docker compose ps
curl http://127.0.0.1:8000/healthz
```

Recreate the SSH tunnel on the local computer:

```bash
ssh -L 8000:127.0.0.1:8000 YOUR_USER@YOUR_VPS_IP
```

### Email sending fails

Verify:

- `AUTOREACH_SIMULATE=false`
- `AGENT4_SIMULATE=false`
- `AGENT4_PROVIDER` matches the credential variables
- `SENDER_EMAIL` is set
- The sender domain/address is verified by the provider
- The API key has permission to send email

Inspect logs:

```bash
sudo docker compose logs --tail=200 autoreach
```

## 16. Useful commands

```bash
# Service status
sudo docker compose ps

# Application logs
sudo docker compose logs -f autoreach

# Redis logs
sudo docker compose logs -f redis

# Restart AutoReach only
sudo docker compose restart autoreach

# Pull the latest image
sudo docker compose pull autoreach

# Display the running image
sudo docker compose images

# Check public process health on the VPS
curl http://127.0.0.1:8000/healthz

# Inspect disk usage
df -h
sudo docker system df
```

## 17. API reference

After startup:

```text
http://localhost:8000/docs
http://localhost:8000/openapi.json
```

For the complete frontend-facing API contract, see
[`FRONTEND_API.md`](./FRONTEND_API.md).
