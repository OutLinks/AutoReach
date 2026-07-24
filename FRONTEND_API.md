# AutoReach Frontend API Contract

This document describes the HTTP API currently implemented by AutoReach. It is
intended to let a frontend be developed independently of the backend repository.

API version: `0.1.0`

## 1. Base URL and interactive schema

Local development:

```text
http://localhost:8000
```

The deployed base URL is environment-specific. Configure it in the frontend,
for example:

```env
VITE_AUTOREACH_API_URL=https://api.example.com
```

FastAPI also exposes:

| URL | Description |
| --- | --- |
| `/docs` | Interactive Swagger UI |
| `/openapi.json` | Machine-readable OpenAPI schema |
| `/redoc` | ReDoc API reference |

The frontend should treat this document as the product contract and
`/openapi.json` as the source for generated clients.

## 2. Authentication

All `/v1/*` endpoints require a bearer token:

```http
Authorization: Bearer YOUR_AUTOREACH_API_SECRET
```

The public endpoints `/`, `/healthz`, `/docs`, `/redoc`, and `/openapi.json` do
not require authentication.

An invalid or missing token returns:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
Content-Type: application/json

{
  "detail": "Invalid or missing bearer token"
}
```

### Important frontend security constraint

`AUTOREACH_API_SECRET` is currently a single administrative service secret. Do
not embed it in a public website bundle. Safe options are:

1. Use the frontend only as a private/internal admin application.
2. Put a backend-for-frontend (BFF) between the browser and AutoReach.
3. Add user authentication and scoped API tokens before exposing the frontend
   to untrusted users.

There are currently no login, logout, refresh-token, user, role, or password
endpoints.

## 3. Browser and CORS configuration

The backend permits browser origins listed in:

```env
AUTOREACH_CORS_ORIGINS=https://app.example.com,http://localhost:3000
```

Only `GET` and `POST` are currently allowed. Browser requests may send the
`Authorization` and `Content-Type` headers. Cookies are not used and credentialed
CORS requests are disabled.

## 4. General conventions

- Request and response bodies use JSON.
- Timestamps are ISO 8601 strings.
- Long-running operations return `202 Accepted` with a durable `JobRecord`.
- Poll `GET /v1/jobs/{job_id}` until the job status is `succeeded` or `failed`.
- Jobs execute one at a time. A job may remain `queued` while earlier jobs run.
- List endpoints use zero-based `offset` pagination.
- The API does not currently return pagination links or cursors.
- Unknown JSON fields are ignored by the current request models.
- There is no API rate limiter in the current backend.
- There are no WebSocket or server-sent event endpoints; use polling.

### Standard validation error

Malformed requests and invalid query parameters return `422`:

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["query", "limit"],
      "msg": "Input should be greater than or equal to 1",
      "input": "0",
      "ctx": {
        "ge": 1
      }
    }
  ]
}
```

## 5. Endpoint summary

| Method | Path | Auth | Success | Purpose |
| --- | --- | --- | --- | --- |
| `GET` | `/` | No | `200` | API discovery |
| `GET` | `/healthz` | No | `200` | Process health check |
| `GET` | `/v1/config` | Yes | `200` | Public runtime configuration |
| `POST` | `/v1/campaigns` | Yes | `202` | Generate a campaign draft |
| `GET` | `/v1/campaigns` | Yes | `200` | List campaign briefs |
| `GET` | `/v1/campaigns/{campaign_id}` | Yes | `200` | Get one campaign |
| `POST` | `/v1/campaigns/{campaign_id}/activate` | Yes | `200` | Activate a campaign |
| `POST` | `/v1/jobs/find` | Yes | `202` | Run lead discovery |
| `POST` | `/v1/jobs/cycle` | Yes | `202` | Run one pipeline cycle |
| `POST` | `/v1/jobs/stages/{stage_name}` | Yes | `202` | Run one pipeline stage |
| `GET` | `/v1/jobs` | Yes | `200` | List durable jobs |
| `GET` | `/v1/jobs/{job_id}` | Yes | `200` | Get/poll one job |
| `GET` | `/v1/leads` | Yes | `200` | List and filter leads |
| `GET` | `/v1/health` | Yes | `200` | Pipeline health snapshot |
| `GET` | `/v1/report` | Yes | `200` | Funnel and conversion report |
| `POST` | `/v1/events/sender` | Yes | `202` | Submit a normalized email event |

## 6. Shared response models

### JobRecord

Every long-running command returns this model immediately:

```ts
type JobStatus = "queued" | "running" | "succeeded" | "failed";

interface JobRecord<TResult = unknown> {
  id: string;
  kind: string;
  status: JobStatus;
  payload: Record<string, unknown>;
  result: TResult | null;
  error: string;
  dedupe_key: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}
```

Example initial response:

```json
{
  "id": "3b9e9a64-374c-4514-8f65-59fffb566a43",
  "kind": "pipeline.find",
  "status": "queued",
  "payload": {},
  "result": null,
  "error": "",
  "dedupe_key": null,
  "created_at": "2026-07-23T10:20:30.123456+00:00",
  "started_at": null,
  "completed_at": null
}
```

Terminal success:

```json
{
  "id": "3b9e9a64-374c-4514-8f65-59fffb566a43",
  "kind": "pipeline.find",
  "status": "succeeded",
  "payload": {},
  "result": {
    "stage": "find",
    "agent": "agent1-lead-finder",
    "processed": 10,
    "succeeded": 10,
    "failed": 0,
    "advanced_ids": [],
    "new_lead_ids": ["lead-id-1"],
    "outcomes": {},
    "errors": [],
    "ok": true,
    "duration_seconds": 1.24
  },
  "error": "",
  "dedupe_key": null,
  "created_at": "2026-07-23T10:20:30.123456+00:00",
  "started_at": "2026-07-23T10:20:30.130000+00:00",
  "completed_at": "2026-07-23T10:20:31.370000+00:00"
}
```

Terminal failure:

```json
{
  "id": "3b9e9a64-374c-4514-8f65-59fffb566a43",
  "kind": "campaign.create",
  "status": "failed",
  "payload": {
    "prompt": "Find property managers in Colombo"
  },
  "result": null,
  "error": "RuntimeError: model provider request failed",
  "dedupe_key": null,
  "created_at": "2026-07-23T10:20:30.123456+00:00",
  "started_at": "2026-07-23T10:20:30.130000+00:00",
  "completed_at": "2026-07-23T10:20:31.370000+00:00"
}
```

The frontend should display `error` only when `status === "failed"`. Do not
assume a fixed result schema without checking `kind`.

### StageResult

The result of `pipeline.find` and `pipeline.stage` jobs:

```ts
interface StageResult {
  stage: PipelineStage;
  agent: string;
  processed: number;
  succeeded: number;
  failed: number;
  advanced_ids: string[];
  new_lead_ids: string[];
  outcomes: Record<string, string>;
  errors: string[];
  ok: boolean;
  duration_seconds: number;
}
```

### CampaignBrief

```ts
type CampaignStatus = "draft" | "active" | "archived";

interface CampaignBrief {
  id: string;
  name: string;
  user_prompt: string;
  summary: string;
  targeting: {
    industries: string[];
    company_sizes: string[];
    locations: string[];
    job_titles: string[];
    exclude_industries: string[];
    b2b_only: boolean;
  };
  messaging: {
    offer: string;
    value_proposition: string;
    tone: string;
    call_to_action: string;
    proof_points: string[];
    forbidden_claims: string[];
  };
  send_policy: {
    emails_per_day: number;
    hourly_send_limit: number;
    followup_days: number[];
  };
  source_urls: string[];
  agent_instructions: {
    lead_finder: string;
    research_analyst: string;
    email_writer: string;
    sender: string;
    reply_handler: string;
  };
  status: CampaignStatus;
  created_at: string;
  updated_at: string;
}
```

Although `archived` is a recognized status, the current API does not expose an
archive endpoint. Campaign creation always produces a `draft`. Activating one
campaign changes the previously active campaign back to `draft`.

### PipelineLead

```ts
type LeadState =
  | "new"
  | "discovered"
  | "researching"
  | "researched"
  | "writing"
  | "ready"
  | "sending"
  | "sent"
  | "following_up"
  | "replied"
  | "handling"
  | "handled"
  | "meeting_booked"
  | "no_reply"
  | "closed"
  | "error"
  | "dead";

interface PipelineLead {
  id: string;
  state: LeadState;
  email: string;
  company: string;
  industry: string;
  quality_score: number;
  company_size_score: number;
  industry_fit_score: number;
  recency_score: number;
  manual_bump: boolean;
  priority: number;
  attempts: number;
  last_error: string;
  retry_after: string | null;
  discovered_at: string | null;
  sent_at: string | null;
  replied_at: string | null;
  state_entered_at: string;
  source_job: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
```

## 7. Public endpoints

### GET `/`

API discovery response:

```json
{
  "name": "AutoReach API",
  "docs": "/docs",
  "health": "/healthz"
}
```

### GET `/healthz`

This only confirms that the API process is responding. It does not indicate
pipeline, provider, Redis, or external API health.

```json
{
  "status": "ok"
}
```

## 8. Runtime configuration

### GET `/v1/config`

Response:

```json
{
  "environment": "production",
  "simulate": false,
  "reply_handling_enabled": false,
  "scheduler_enabled": true,
  "scheduler_timezone": "Asia/Colombo",
  "stages": [
    "find",
    "research",
    "write",
    "send",
    "followup",
    "reply"
  ]
}
```

The endpoint is read-only. There is currently no API for updating runtime
settings, email providers, or provider credentials.

## 9. Campaign endpoints

### POST `/v1/campaigns`

Uses the configured LLM to compile a free-form prompt into a reviewable campaign
draft.

Request:

```json
{
  "prompt": "Find property-management companies in Colombo with 10 to 100 employees and contact founders about our maintenance automation platform."
}
```

Constraints:

- `prompt` is required.
- Minimum length: 10 characters.
- Maximum length: 20,000 characters.

Response: `202 Accepted` with `JobRecord<CampaignBrief>`.

The campaign does not appear in `GET /v1/campaigns` until the job succeeds.
Read the created campaign from `job.result`, then present it for human review.

```bash
curl -X POST "$API_URL/v1/campaigns" \
  -H "Authorization: Bearer $AUTOREACH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Find property-management companies in Colombo with 10 to 100 employees."
  }'
```

### GET `/v1/campaigns`

Query parameters:

| Parameter | Default | Allowed |
| --- | --- | --- |
| `limit` | `50` | `1` to `200` |
| `offset` | `0` | `0` or greater |

Response:

```json
{
  "items": [
    {
      "id": "campaign-uuid",
      "name": "Colombo property managers",
      "user_prompt": "Find property-management companies...",
      "summary": "Outreach to Colombo property managers.",
      "targeting": {
        "industries": ["property management"],
        "company_sizes": ["10-100"],
        "locations": ["Colombo"],
        "job_titles": ["founder"],
        "exclude_industries": [],
        "b2b_only": true
      },
      "messaging": {
        "offer": "Maintenance automation platform",
        "value_proposition": "Reduce manual coordination",
        "tone": "professional",
        "call_to_action": "Book a short call",
        "proof_points": [],
        "forbidden_claims": []
      },
      "send_policy": {
        "emails_per_day": 20,
        "hourly_send_limit": 5,
        "followup_days": [3, 7, 14]
      },
      "source_urls": [],
      "agent_instructions": {
        "lead_finder": "Find matching companies and decision makers.",
        "research_analyst": "Research relevant operational pain points.",
        "email_writer": "Write concise personalized outreach.",
        "sender": "Follow the approved send policy.",
        "reply_handler": "Classify and route replies."
      },
      "status": "draft",
      "created_at": "2026-07-23T10:20:30.123456",
      "updated_at": "2026-07-23T10:20:30.123456"
    }
  ]
}
```

This response does not currently include a `total` value.

### GET `/v1/campaigns/{campaign_id}`

Success: `200` with a `CampaignBrief`.

Not found:

```http
HTTP/1.1 404 Not Found

{
  "detail": "Campaign not found"
}
```

### POST `/v1/campaigns/{campaign_id}/activate`

Activates the reviewed campaign synchronously. No request body is required.

Success: `200` with the activated `CampaignBrief`, where:

```json
{
  "status": "active"
}
```

Only one campaign can be active. Activating another campaign returns the
previously active campaign to `draft`.

Not found:

```json
{
  "detail": "Campaign not found"
}
```

## 10. Pipeline job endpoints

### Pipeline stages

```ts
type PipelineStage =
  | "find"
  | "research"
  | "write"
  | "send"
  | "followup"
  | "reply";
```

| Stage | Agent | Input | Typical success state |
| --- | --- | --- | --- |
| `find` | Agent 1 | Campaign/search configuration | `discovered` |
| `research` | Agent 2 | `discovered` | `researched` |
| `write` | Agent 3 | `researched` | `ready` |
| `send` | Agent 4 | `ready` | `sent` |
| `followup` | Agent 4 | `sent` | `following_up` |
| `reply` | Agent 5 | `replied` | `handled` |

The reply stage may be disabled. Check
`GET /v1/config -> reply_handling_enabled`.

### POST `/v1/jobs/find`

Queues lead discovery. No request body is required.

Response: `202 Accepted` with `JobRecord<StageResult>`.

```bash
curl -X POST "$API_URL/v1/jobs/find" \
  -H "Authorization: Bearer $AUTOREACH_TOKEN"
```

### POST `/v1/jobs/cycle`

Queues one pass through the forward pipeline. The cycle runs stages in this
order:

```text
reply -> followup -> send -> write -> research
```

The `reply` stage is omitted when reply handling is disabled. Lead discovery is
not part of a cycle and must be triggered through `/v1/jobs/find`.

Response: `202 Accepted` with:

```ts
type CycleResult = Partial<Record<PipelineStage, StageResult>>;
type CycleJob = JobRecord<CycleResult>;
```

Example successful result:

```json
{
  "reply": {
    "stage": "reply",
    "agent": "agent5-reply-handler",
    "processed": 1,
    "succeeded": 1,
    "failed": 0,
    "advanced_ids": ["lead-1"],
    "new_lead_ids": [],
    "outcomes": {
      "lead-1": "handled"
    },
    "errors": [],
    "ok": true,
    "duration_seconds": 0.8
  },
  "send": {
    "stage": "send",
    "agent": "agent4-sender",
    "processed": 3,
    "succeeded": 3,
    "failed": 0,
    "advanced_ids": ["lead-2", "lead-3", "lead-4"],
    "new_lead_ids": [],
    "outcomes": {},
    "errors": [],
    "ok": true,
    "duration_seconds": 1.1
  }
}
```

### POST `/v1/jobs/stages/{stage_name}`

Queues exactly one stage. No request body is required.

```bash
curl -X POST "$API_URL/v1/jobs/stages/research" \
  -H "Authorization: Bearer $AUTOREACH_TOKEN"
```

Response: `202 Accepted` with `JobRecord<StageResult>`.

Unknown stage:

```http
HTTP/1.1 404 Not Found

{
  "detail": "Unknown pipeline stage"
}
```

### GET `/v1/jobs`

Returns newest jobs first.

Query parameters:

| Parameter | Default | Allowed |
| --- | --- | --- |
| `limit` | `50` | `1` to `200` |
| `offset` | `0` | `0` or greater |

Response:

```json
{
  "items": [
    {
      "id": "job-uuid",
      "kind": "pipeline.cycle",
      "status": "running",
      "payload": {},
      "result": null,
      "error": "",
      "dedupe_key": null,
      "created_at": "2026-07-23T10:20:30.123456+00:00",
      "started_at": "2026-07-23T10:20:30.130000+00:00",
      "completed_at": null
    }
  ]
}
```

This response does not currently include a `total` value.

Known job kinds:

| Kind | Created by |
| --- | --- |
| `campaign.create` | `POST /v1/campaigns` |
| `pipeline.find` | `POST /v1/jobs/find` |
| `pipeline.cycle` | `POST /v1/jobs/cycle` |
| `pipeline.stage` | `POST /v1/jobs/stages/{stage_name}` |
| `pipeline.tick` | Internal scheduler |
| `sender.event` | `POST /v1/events/sender` |

### GET `/v1/jobs/{job_id}`

Use this endpoint to poll a long-running command.

Success: `200` with `JobRecord`.

Not found:

```json
{
  "detail": "Job not found"
}
```

Recommended polling behavior:

1. Poll every 1 second initially.
2. Increase to 2–5 seconds for long-running jobs.
3. Stop when status is `succeeded` or `failed`.
4. Stop polling if the component unmounts or the user navigates away.
5. Treat network errors as retryable; treat `401` as an authentication failure.

There is currently no cancel, retry, delete, or priority endpoint for jobs.

## 11. Lead endpoint

### GET `/v1/leads`

Query parameters:

| Parameter | Default | Allowed |
| --- | --- | --- |
| `state` | none | Exact lead-state string |
| `limit` | `100` | `1` to `500` |
| `offset` | `0` | `0` or greater |

Example:

```http
GET /v1/leads?state=ready&limit=50&offset=0
```

Response:

```json
{
  "items": [
    {
      "id": "lead-uuid",
      "state": "ready",
      "email": "founder@example.com",
      "company": "Example Property Management",
      "industry": "property management",
      "quality_score": 0.87,
      "company_size_score": 0.7,
      "industry_fit_score": 0.9,
      "recency_score": 1.0,
      "manual_bump": false,
      "priority": 0.84,
      "attempts": 0,
      "last_error": "",
      "retry_after": null,
      "discovered_at": "2026-07-23T08:00:00+00:00",
      "sent_at": null,
      "replied_at": null,
      "state_entered_at": "2026-07-23T09:00:00+00:00",
      "source_job": "campaign-uuid",
      "metadata": {
        "campaign_id": "campaign-uuid"
      },
      "created_at": "2026-07-23T08:00:00+00:00",
      "updated_at": "2026-07-23T09:00:00+00:00"
    }
  ],
  "total": 1
}
```

An unrecognized `state` is not rejected; it produces an empty result.

There is currently no endpoint to get one lead, edit a lead, delete a lead,
manually bump a lead, list its events, or retrieve its generated research/email.

## 12. Monitoring endpoints

### GET `/v1/health`

This is different from `/healthz`: it reports pipeline queues, error rates,
circuit breakers, and dead-letter backlog.

```ts
interface PipelineHealth {
  healthy: boolean;
  stages: Array<{
    stage: "research" | "write" | "send" | "followup" | "reply";
    queue_depth: number;
    in_progress: number;
    error_rate: number;
    circuit: "closed" | "open" | "half_open";
    oldest_wait_hours: number;
    bottleneck: boolean;
  }>;
  alerts: string[];
  dead_letter_count: number;
  computed_at: string;
}
```

Example:

```json
{
  "healthy": true,
  "stages": [
    {
      "stage": "research",
      "queue_depth": 3,
      "in_progress": 0,
      "error_rate": 0.0,
      "circuit": "closed",
      "oldest_wait_hours": 0.4,
      "bottleneck": false
    }
  ],
  "alerts": [],
  "dead_letter_count": 0,
  "computed_at": "2026-07-23T10:20:30.123456"
}
```

The `find` stage is not included because it does not consume a stored lead
queue.

### GET `/v1/report`

```ts
interface PipelineReport {
  funnel: Partial<Record<LeadState, number>>;
  sent_today: number;
  replies_today: number;
  meetings_booked: number;
  reply_rate: number;
  meeting_rate: number;
  dead_letter_count: number;
  alerts: string[];
  optimizations: string[];
  generated_at: string;
}
```

Example:

```json
{
  "funnel": {
    "discovered": 10,
    "researched": 8,
    "ready": 5,
    "sent": 20,
    "replied": 3
  },
  "sent_today": 20,
  "replies_today": 3,
  "meetings_booked": 1,
  "reply_rate": 0.15,
  "meeting_rate": 0.05,
  "dead_letter_count": 0,
  "alerts": [],
  "optimizations": [],
  "generated_at": "2026-07-23T10:20:30.123456"
}
```

Despite the current field names `sent_today` and `replies_today`, the backend
calculates these values cumulatively from the audit log, not strictly for the
current calendar day.

## 13. Sender event endpoint

### POST `/v1/events/sender`

This endpoint accepts an already-normalized email event. It does not verify
SendGrid, Mailgun, Postmark, Resend, SES, or other provider webhook signatures.
A trusted webhook adapter must validate the provider signature before forwarding
an event here.

Do not call this endpoint directly from an email provider or an untrusted public
browser.

Request:

```ts
type SenderEventRequest =
  | {
      event: "reply";
      sent_email_id: string;
      detail?: string;
    }
  | {
      event: "bounce";
      sent_email_id: string;
      detail?: string;
      bounce_type?: "hard" | "soft";
    }
  | {
      event: "complaint";
      sent_email_id: string;
      detail?: string;
    }
  | {
      event: "open";
      sent_email_id: string;
      detail?: string;
    }
  | {
      event: "click";
      sent_email_id: string;
      detail?: string;
      url?: string;
    };
```

Backend validation rules:

| Field | Rule |
| --- | --- |
| `event` | Required: `reply`, `bounce`, `complaint`, `open`, or `click` |
| `sent_email_id` | Required, 1 to 200 characters |
| `detail` | Optional, maximum 10,000 characters |
| `bounce_type` | Optional, `hard` or `soft`; defaults to `hard` |
| `url` | Optional, maximum 4,000 characters |

Bounce example:

```json
{
  "event": "bounce",
  "sent_email_id": "sent-email-uuid",
  "bounce_type": "hard",
  "detail": "550 mailbox does not exist"
}
```

Click example:

```json
{
  "event": "click",
  "sent_email_id": "sent-email-uuid",
  "url": "https://example.com/demo"
}
```

Response: `202 Accepted` with a `JobRecord`. Successful result shapes differ:

| Event | Typical `job.result` |
| --- | --- |
| `reply` | Reply notification object, or `null` for an unknown sent-email ID |
| `bounce` | `{ "disposition": "suppressed" \| "retry" \| "ignored" }` |
| `complaint` | `{ "recorded": true }` |
| `open` | `{ "recorded": true }` |
| `click` | `{ "recorded": true }` |

A reply notification has this shape:

```json
{
  "lead_id": "lead-uuid",
  "sent_email_id": "sent-email-uuid",
  "email_id": "written-email-uuid",
  "message_id": "provider-message-id",
  "recipient": "lead@example.com",
  "snippet": "Yes, please send more information.",
  "detected_at": "2026-07-23T10:20:30.123456"
}
```

## 14. Complete TypeScript client

The following dependency-free client covers every implemented endpoint:

```ts
export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type PipelineStage =
  | "find"
  | "research"
  | "write"
  | "send"
  | "followup"
  | "reply";

export interface JobRecord<TResult = unknown> {
  id: string;
  kind: string;
  status: JobStatus;
  payload: Record<string, unknown>;
  result: TResult | null;
  error: string;
  dedupe_key: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export class AutoReachApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(`AutoReach API request failed with HTTP ${status}`);
  }
}

export class AutoReachClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  private async request<T>(
    path: string,
    init: RequestInit = {},
    authenticated = true,
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    if (authenticated) headers.set("Authorization", `Bearer ${this.token}`);

    const response = await fetch(`${this.baseUrl.replace(/\/$/, "")}${path}`, {
      ...init,
      headers,
    });

    const contentType = response.headers.get("content-type") ?? "";
    const body = contentType.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      throw new AutoReachApiError(response.status, body);
    }
    return body as T;
  }

  root() {
    return this.request<{ name: string; docs: string; health: string }>(
      "/",
      {},
      false,
    );
  }

  healthz() {
    return this.request<{ status: "ok" }>("/healthz", {}, false);
  }

  config() {
    return this.request<{
      environment: string;
      simulate: boolean;
      reply_handling_enabled: boolean;
      scheduler_enabled: boolean;
      scheduler_timezone: string;
      stages: PipelineStage[];
    }>("/v1/config");
  }

  createCampaign(prompt: string) {
    return this.request<JobRecord<CampaignBrief>>("/v1/campaigns", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
  }

  listCampaigns(limit = 50, offset = 0) {
    return this.request<{ items: CampaignBrief[] }>(
      `/v1/campaigns?limit=${limit}&offset=${offset}`,
    );
  }

  getCampaign(campaignId: string) {
    return this.request<CampaignBrief>(
      `/v1/campaigns/${encodeURIComponent(campaignId)}`,
    );
  }

  activateCampaign(campaignId: string) {
    return this.request<CampaignBrief>(
      `/v1/campaigns/${encodeURIComponent(campaignId)}/activate`,
      { method: "POST" },
    );
  }

  runFind() {
    return this.request<JobRecord<StageResult>>("/v1/jobs/find", {
      method: "POST",
    });
  }

  runCycle() {
    return this.request<
      JobRecord<Partial<Record<PipelineStage, StageResult>>>
    >("/v1/jobs/cycle", { method: "POST" });
  }

  runStage(stage: PipelineStage) {
    return this.request<JobRecord<StageResult>>(
      `/v1/jobs/stages/${encodeURIComponent(stage)}`,
      { method: "POST" },
    );
  }

  listJobs(limit = 50, offset = 0) {
    return this.request<{ items: JobRecord[] }>(
      `/v1/jobs?limit=${limit}&offset=${offset}`,
    );
  }

  getJob<TResult = unknown>(jobId: string) {
    return this.request<JobRecord<TResult>>(
      `/v1/jobs/${encodeURIComponent(jobId)}`,
    );
  }

  listLeads(options: {
    state?: LeadState;
    limit?: number;
    offset?: number;
  } = {}) {
    const query = new URLSearchParams();
    if (options.state) query.set("state", options.state);
    query.set("limit", String(options.limit ?? 100));
    query.set("offset", String(options.offset ?? 0));
    return this.request<{ items: PipelineLead[]; total: number }>(
      `/v1/leads?${query.toString()}`,
    );
  }

  pipelineHealth() {
    return this.request<PipelineHealth>("/v1/health");
  }

  report() {
    return this.request<PipelineReport>("/v1/report");
  }

  submitSenderEvent(event: SenderEventRequest) {
    return this.request<JobRecord>("/v1/events/sender", {
      method: "POST",
      body: JSON.stringify(event),
    });
  }

  async waitForJob<TResult>(
    jobId: string,
    options: {
      intervalMs?: number;
      timeoutMs?: number;
      signal?: AbortSignal;
    } = {},
  ): Promise<JobRecord<TResult>> {
    const intervalMs = options.intervalMs ?? 1_000;
    const timeoutMs = options.timeoutMs ?? 10 * 60_000;
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
      if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const job = await this.getJob<TResult>(jobId);
      if (job.status === "succeeded") return job;
      if (job.status === "failed") {
        throw new Error(job.error || `Job ${job.id} failed`);
      }
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(resolve, intervalMs);
        options.signal?.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timer);
            reject(new DOMException("Aborted", "AbortError"));
          },
          { once: true },
        );
      });
    }
    throw new Error(`Timed out waiting for job ${jobId}`);
  }
}
```

The snippet references the interfaces defined earlier in this document:
`CampaignBrief`, `StageResult`, `LeadState`, `PipelineLead`, `PipelineHealth`,
`PipelineReport`, and `SenderEventRequest`.

### Example frontend workflow

```ts
const client = new AutoReachClient(
  import.meta.env.VITE_AUTOREACH_API_URL,
  sessionStorage.getItem("autoreach_token") ?? "",
);

// 1. Create and wait for the campaign draft.
const createJob = await client.createCampaign(
  "Find property managers in Colombo and offer maintenance automation.",
);
const completed = await client.waitForJob<CampaignBrief>(createJob.id);
const draft = completed.result;
if (!draft) throw new Error("Campaign job completed without a campaign");

// 2. Show draft to the human and activate after confirmation.
await client.activateCampaign(draft.id);

// 3. Discover leads and wait for completion.
const findJob = await client.runFind();
await client.waitForJob<StageResult>(findJob.id);

// 4. Refresh dashboard data.
const [leads, report, health] = await Promise.all([
  client.listLeads(),
  client.report(),
  client.pipelineHealth(),
]);
```

## 15. Suggested frontend pages

The current API supports these pages without backend additions:

1. **Dashboard** — `/v1/report`, `/v1/health`, and recent `/v1/jobs`.
2. **Campaign list** — `/v1/campaigns`.
3. **Campaign creation/review** — create job, poll it, display `CampaignBrief`,
   then activate it.
4. **Lead list** — `/v1/leads` with state filters and pagination.
5. **Pipeline controls** — run find, cycle, or individual stages.
6. **Job activity** — list and poll durable jobs.

## 16. Backend gaps relevant to richer frontend work

These capabilities are not implemented and should not be assumed by the
frontend:

- User authentication, accounts, sessions, roles, and scoped permissions.
- Creating or rotating API keys through the API.
- Configuring email providers or credentials through the API.
- Campaign editing, archiving, deleting, pausing, or deactivating.
- Lead detail, editing, deletion, manual bump, audit history, and suppression.
- Reading generated research, email drafts, sent messages, or conversations.
- Job cancellation, deletion, retry, or server-push progress.
- Totals for campaign and job pagination.
- Provider webhook signature validation.
- File uploads.
- Bulk actions.

Design UI controls for these features as disabled or omit them until matching
backend endpoints exist.

## 17. Environment checklist for a separate frontend

Backend:

```env
AUTOREACH_API_SECRET=replace-with-a-long-random-value
AUTOREACH_CORS_ORIGINS=https://app.example.com,http://localhost:3000
```

Frontend:

```env
VITE_AUTOREACH_API_URL=https://api.example.com
```

For a public frontend, do not define the backend secret as a `VITE_*`,
`NEXT_PUBLIC_*`, or other build-time public environment variable. Store it only
in a trusted server/BFF environment.
