# Cloudflare Containers migration deployment

> Status: foundation only. The Worker and Container wrapper are implemented,
> but important Python paths still use local SQLite and filesystem artifacts.
> Agent 1 has a D1 pipeline adapter; Agent 2 indexes R2 research profiles in
> D1; Agent 3 reads that durable chain and writes D1 email records; and Agent 4
> reads those D1 records and stores sender state in D1. The orchestrator,
> interactive workflow, delayed sequence registration, and Agent 5 repositories
> have not cut over. Do not deploy this
> revision as production until that migration is complete.

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

## Secrets

Set Worker Secrets; never send these values through `PATCH /v1/settings` in a
Cloudflare production deployment:

```bash
npx wrangler secret put AUTOREACH_API_TOKEN
npx wrangler secret put AUTOREACH_INTERNAL_HMAC_SECRET
npx wrangler secret put SMTP_PASSWORD
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
.venv/bin/python -m unittest discover -s tests -v
```

For the Worker wrapper, install Node dependencies and use Wrangler. Docker must
be running because the Worker configuration refers to `./Dockerfile`:

```bash
npm install
npm run cf:check
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

After the durable-storage cutover lands:

```bash
npm run cf:deploy
curl -fsS -H "Authorization: Bearer $AUTOREACH_API_TOKEN" \
  https://YOUR_WORKER/v1/config
```

Create a simulated job, then poll it through the Worker. Queue retries appear
in Workers/Queues observability; messages exceeding retry limits go to
`autoreach-jobs-dlq`. Cron runs every five minutes in UTC. Follow-up Workflows
must be inspected with `wrangler workflows instances describe` and tested by
creating a near-future `due_at_utc` job before testing a multi-day send.

## Cutover and rollback

1. Quiesce the Compose deployment and back up its `/data` volume and Redis.
2. Export SQLite tables and runtime artifacts, import relational records to D1,
   and upload artifact bodies to R2 with checksums.
3. Validate row counts, lead state counts, outbox entries, and sender
   idempotency keys before enabling Queue dispatch.
4. Deploy the Worker with the old Compose stack still stopped but recoverable.
5. Roll back by disabling Worker routes/Queue dispatch and restoring the saved
   Compose volume. Do not run both writers against the same campaign data.
