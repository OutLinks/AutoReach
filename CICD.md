# AutoReach CI/CD

The workflow in `.github/workflows/ci-cd.yml`:

1. runs all Python tests on every pull request;
2. runs tests on every push to `main`;
3. builds `linux/amd64` and `linux/arm64` Docker images after tests pass;
4. publishes `janudax/autoreach:latest` and an immutable
   `janudax/autoreach:sha-<commit>` tag; and
5. connects to the production VPS, pulls `latest`, recreates the AutoReach
   container, and verifies `/healthz`.

Publishing and deployment never run for pull requests, so repository secrets are
not exposed to untrusted pull-request code.

## GitHub secrets

Open the GitHub repository and go to:

```text
Settings → Secrets and variables → Actions → New repository secret
```

Create:

| Secret | Value |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub username, currently `janudax` |
| `DOCKERHUB_TOKEN` | Docker Hub access token with permission to push `janudax/autoreach` |
| `VPS_HOST` | `169.58.69.5` |
| `VPS_PORT` | `22` unless SSH uses another port |
| `VPS_USER` | `root` for the current deployment |
| `VPS_SSH_PRIVATE_KEY` | Complete private key for the VPS deployment key |

For optional deployment approvals, create a GitHub environment named
`production` and add required reviewers under:

```text
Settings → Environments → production
```

## Create a dedicated VPS deployment key

Generate the key on a trusted computer:

```bash
ssh-keygen -t ed25519 -C "github-autoreach-deploy" \
  -f autoreach-github-deploy
```

Install the public key on the VPS:

```bash
ssh-copy-id -i autoreach-github-deploy.pub root@169.58.69.5
```

Copy the full contents of `autoreach-github-deploy` into the
`VPS_SSH_PRIVATE_KEY` GitHub secret. Do not commit either key to this repository.

Verify the key before relying on the workflow:

```bash
ssh -i autoreach-github-deploy root@169.58.69.5
```

## VPS Compose deployment

The deployment workflow uploads the repository's `compose.yaml` to
`/root/outreach-agent/compose.yaml`, validates it, and then updates the complete
stack. It runs both AutoReach and its Redis working queue, preserves their data
in named volumes, and binds the API privately on `127.0.0.1:8000`.

The deployment directory must exist before the first deployment:

```bash
mkdir -p /root/outreach-agent
```

The Docker and Compose commands must work for `VPS_USER` without an interactive
password prompt. The current workflow expects `VPS_USER=root`.

Operational settings remain in SQLite. Configure the Docker-network Redis URL
once through the API:

```bash
curl -sS -X PATCH http://127.0.0.1:8000/v1/settings \
  -H "Content-Type: application/json" \
  -d '{"values":{"redis_url":"redis://redis:6379/0"}}'
```

## First deployment

Commit the workflow and push or merge it into `main`. Watch it under the
repository's **Actions** tab. A successful run ends with:

```text
Test → Publish Docker image → Deploy to VPS
```

After deployment:

```bash
curl http://169.58.69.5/healthz
curl http://169.58.69.5/
```

## Rollback

Every deployment publishes an immutable SHA tag. To roll back, replace `latest`
in the VPS Compose file temporarily with a known tag:

```yaml
image: janudax/autoreach:sha-FULL_GITHUB_COMMIT_SHA
```

Then run:

```bash
cd /root/outreach-agent
docker compose pull autoreach
docker compose up -d --force-recreate autoreach
curl -fsS http://127.0.0.1:8000/healthz
```
