# Cloudflare Containers deployment

The production runtime keeps FastAPI and all five Python agents in one stable
Cloudflare Container. The TypeScript Worker owns authentication, container
routing, Cron, Queue consumption, delayed Workflows, and D1/R2 access. Local
SQLite, Redis, JSONL, and the in-process scheduler remain development adapters
only; every production backend is selected explicitly with `d1`/`r2` container
environment variables.

## Resources

Create these resources in the target Cloudflare account before replacing the
placeholder D1 ID in `wrangler.jsonc`:

```bash
npx wrangler d1 create autoreach
npx wrangler r2 bucket create autoreach-artifacts
npx wrangler queues create autoreach-jobs
npx wrangler queues create autoreach-jobs-dlq
```

Copy the D1 `database_id` into `wrangler.jsonc`; do not commit an account-
specific ID to a shared branch if the deployment environment uses separate
resources. Use an environment-specific Wrangler config instead.

Apply D1 migrations after the database is bound:

```bash
npx wrangler d1 migrations apply autoreach --remote
```

Run `npm run cf:preflight` after migrations and secrets are configured. It
rejects the placeholder database ID, missing minimum secrets, and unapplied
remote migrations.

## Secrets

Set Worker Secrets; never send these values through `PATCH /v1/settings` in a
Cloudflare production deployment:

```bash
npx wrangler secret put AUTOREACH_API_TOKEN
npx wrangler secret put AUTOREACH_INTERNAL_HMAC_SECRET
npx wrangler secret put SMTP_PASSWORD
# SMTP connection values are injected the same way:
npx wrangler secret put SMTP_HOST
npx wrangler secret put SMTP_PORT
npx wrangler secret put SMTP_USERNAME
# Add only the provider secrets actively used, for example:
npx wrangler secret put ANTHROPIC_API_KEY
npx wrangler secret put TAVILY_API_KEY
```

The Worker accepts a bearer token for public API traffic and strips any client
attempt to supply the internal HMAC headers. The internal secret is passed to
the Python container only for Queue-to-container job execution.

## Local development

Python remains the local development path:

```bash
AUTOREACH_DATA_DIR=.data .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
.venv/bin/python -m pytest -q
```

For the Worker wrapper, install Node dependencies and use Wrangler. Docker must
be running because the Worker configuration refers to `./Dockerfile`:

```bash
npm install
npm run cf:verify
npm run cf:dev
```

`wrangler deploy --dry-run` validates Worker bundling and attempts the Linux
amd64 container image build. Test the image explicitly with:

```bash
docker buildx build --platform linux/amd64 --load -t autoreach:cloudflare .
docker run --rm -p 8000:8000 autoreach:cloudflare
curl -fsS http://127.0.0.1:8000/healthz
```

## Deployment and verification

```bash
npm run cf:preflight
npm run cf:deploy
curl -fsS https://YOUR_WORKER/healthz
curl -fsS -H "Authorization: Bearer $AUTOREACH_API_TOKEN" \
  https://YOUR_WORKER/v1/config
```

Initialize D1-backed non-secret settings once (omit secret-shaped fields):

```bash
curl -fsS -X POST https://YOUR_WORKER/v1/setup \
  -H "Authorization: Bearer $AUTOREACH_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"settings":{"simulate":true,"scheduler_timezone":"UTC"}}'
```

Create and poll a test job:

```bash
JOB_ID=$(curl -fsS -X POST https://YOUR_WORKER/v1/jobs/find \
  -H "Authorization: Bearer $AUTOREACH_API_TOKEN" | jq -r .id)
curl -fsS -H "Authorization: Bearer $AUTOREACH_API_TOKEN" \
  "https://YOUR_WORKER/v1/jobs/$JOB_ID"
```

Keep simulation enabled for the first Queue/Cron test. Enable
`scheduler_enabled` through `PATCH /v1/settings`, wait for the five-minute Cron,
and confirm that only one `pipeline.tick` job exists for a given configured
timezone/local hour. Cron normalizes its timestamp to UTC and records the IANA
timezone in the job payload.

For a delayed-email test, send one approved simulated email and inspect the
new `sequence_states.workflow_instance_id`. Describe that instance with
`npx wrangler workflows instances describe autoreach-followups INSTANCE_ID`.
For a quick non-production test, use a temporary sequence deadline a few
minutes ahead; verify one `sender.followup` job and one deterministic sender
idempotency key. Restore the campaign cadence before enabling live sends.

Queue delivery is at least once. D1 claim leases, deterministic event/send keys,
and pre-provider capacity reservations make retries converge. Queue retries are
visible in Workers observability; attempts beyond the configured limit move to
`autoreach-jobs-dlq`. A `sending` or `delivery_ambiguous` reservation is not
automatically resent—reconcile it with the provider before a manual retry.

## Cutover and rollback

1. Disable the local scheduler and quiesce Compose. Back up its `/data` volume,
   agent output directories, and Redis before exporting anything.
2. Export canonical SQLite tables to newline-delimited JSON or SQL. Preserve
   primary keys, timestamps, lead states, unique provider event IDs, and sender
   idempotency keys; do not import plaintext credential settings.
3. Import relational rows into D1 and upload research/source bodies to R2 under
   stable keys. Populate `agent_artifacts` with the R2 key and SHA-256 checksum.
4. Compare per-table rows, per-state lead counts, artifact checksums, active
   sequences, suppressions, and sender idempotency keys. Resolve ambiguous sends
   before continuing.
5. Apply migrations, run `npm run cf:preflight`, and deploy with simulation
   enabled. Verify health, direct jobs, Queue retries, Cron dedupe, and a short
   Workflow before enabling credentials/live sending.
6. Keep Compose stopped but recoverable through the first verified campaign.
   Roll back by disabling the Worker route and Queue consumer, then restore the
   saved Compose data. Never run both writers against the same campaign.
