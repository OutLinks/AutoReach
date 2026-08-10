import { Container, getContainer } from "@cloudflare/containers";
import {
  WorkflowEntrypoint,
  type WorkflowEvent,
  type WorkflowStep,
} from "cloudflare:workers";

import { dispatchPendingJobs, enqueueScheduledTick, handleArtifactBridge, handleD1Bridge, handleEmailWriterBridge, handleLeadPipelineBridge, handleOrchestratorBridge, handleResearchIndexBridge, handleSenderBridge } from "./d1-bridge";
import { handleConfigStateBridge, handleWorkflowBridge } from "./workflow-bridge";
import { handleReplyBridge } from "./reply-bridge";

const PRIMARY_CONTAINER_NAME = "autoreach-primary";
const INTERNAL_HEADER_NAMES = [
  "x-autoreach-internal-signature",
  "x-autoreach-internal-timestamp",
];

const CONTAINER_SECRET_NAMES = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "OPENROUTER_API_KEY",
  "FIRECRAWL_API_KEY",
  "TAVILY_API_KEY",
  "HUNTER_API_KEY",
  "ABSTRACT_API_KEY",
  "GNEWS_API_KEY",
  "GITHUB_API_KEY",
  "WAPPALYZER_API_KEY",
  "CRUNCHBASE_API_KEY",
  "WHOISXML_API_KEY",
  "SECURITYTRAILS_API_KEY",
  "SMTP_HOST",
  "SMTP_PORT",
  "SMTP_USERNAME",
  "SMTP_PASSWORD",
  "GMAIL_ACCESS_TOKEN",
  "SENDGRID_API_KEY",
  "MAILGUN_API_KEY",
  "POSTMARK_SERVER_TOKEN",
  "RESEND_API_KEY",
  "INSTANTLY_API_KEY",
  "OUTREACH_ACCESS_TOKEN",
  "AWS_ACCESS_KEY_ID",
  "AWS_SECRET_ACCESS_KEY",
  "AWS_SES_REGION",
] as const;

export interface Env {
  AUTOREACH_CONTAINER: DurableObjectNamespace<AutoReachContainer>;
  AUTOREACH_DB: D1Database;
  AUTOREACH_ARTIFACTS: R2Bucket;
  AUTOREACH_JOBS: Queue<JobMessage>;
  AUTOREACH_FOLLOWUPS: Workflow;
  AUTOREACH_ENV: string;
  /** User-facing API bearer token. Configure as a Worker Secret. */
  AUTOREACH_API_TOKEN?: string;
  /** Shared only by the Worker and the Python container. */
  AUTOREACH_INTERNAL_HMAC_SECRET: string;
  [secretName: string]: unknown;
}

interface JobMessage {
  job_id: string;
}

interface FollowupParams {
  job_id: string;
  due_at_utc: string;
}

/**
 * The single production execution container. Persistent data belongs in D1/R2,
 * not in the VM filesystem or Durable Object storage.
 */
export class AutoReachContainer extends Container {
  defaultPort = 8000;
  sleepAfter = "15m";
  envVars = {
    PORT: "8000",
    AUTOREACH_RUNTIME: "cloudflare-container",
    AUTOREACH_EXECUTOR_MODE: "external",
    AUTOREACH_SCHEDULER_ENABLED: "false",
    AUTOREACH_STORAGE_BACKEND: "d1",
    AUTOREACH_LEAD_PIPELINE_BACKEND: "d1",
    AUTOREACH_ARTIFACT_BACKEND: "r2",
    AUTOREACH_EMAIL_WRITER_STORAGE_BACKEND: "d1",
    AUTOREACH_RESEARCH_READER_BACKEND: "d1",
    AUTOREACH_SENDER_STORAGE_BACKEND: "d1",
    AUTOREACH_REPLY_STORAGE_BACKEND: "d1",
    AUTOREACH_REPLY_SOURCE_BACKEND: "d1",
  };
}

AutoReachContainer.outboundByHost = {
  "autoreach.storage": (request, env) => {
    const pathname = new URL(request.url).pathname;
    return pathname.startsWith("/v1/artifacts/")
      ? handleArtifactBridge(request, env)
      : pathname.startsWith("/v1/lead-pipeline/")
      ? handleLeadPipelineBridge(request, env)
      : pathname.startsWith("/v1/sender/")
      ? handleSenderBridge(request, env)
      : pathname.startsWith("/v1/email-writer/")
      ? handleEmailWriterBridge(request, env)
      : pathname.startsWith("/v1/research-index/")
      ? handleResearchIndexBridge(request, env)
      : pathname.startsWith("/v1/orchestrator/")
      ? handleOrchestratorBridge(request, env)
      : pathname.startsWith("/v1/workflows/")
      ? handleWorkflowBridge(request, env)
      : pathname.startsWith("/v1/config-state/")
      ? handleConfigStateBridge(request, env)
      : pathname.startsWith("/v1/replies/")
      ? handleReplyBridge(request, env)
      : handleD1Bridge(request, env);
  },
};

/** Schedules a follow-up without depending on a running Python process. */
export class FollowupWorkflow extends WorkflowEntrypoint<Env, FollowupParams> {
  override async run(event: Readonly<WorkflowEvent<FollowupParams>>, step: WorkflowStep) {
    const { job_id: jobId, due_at_utc: dueAtUtc } = event.payload;
    const dueAt = Date.parse(dueAtUtc);
    if (Number.isNaN(dueAt)) {
      throw new Error("due_at_utc must be an ISO-8601 UTC timestamp");
    }

    await step.sleepUntil("wait for follow-up", dueAt);
    await step.do(
      "dispatch due follow-up",
      { retries: { limit: 8, delay: "30 seconds", backoff: "exponential" } },
      async () => {
        await this.env.AUTOREACH_JOBS.send({ job_id: jobId });
        return { job_id: jobId, dispatched_at: new Date().toISOString() };
      },
    );
  }
}

function bearerAuthorized(request: Request, env: Env): boolean {
  const expected = env.AUTOREACH_API_TOKEN;
  if (!expected) {
    return false;
  }
  return request.headers.get("authorization") === `Bearer ${expected}`;
}

function sanitizedRequest(request: Request): Request {
  const headers = new Headers(request.headers);
  for (const header of INTERNAL_HEADER_NAMES) {
    headers.delete(header);
  }
  return new Request(request, { headers });
}

function containerEnvironment(env: Env): Record<string, string> {
  const variables: Record<string, string> = {
    PORT: "8000",
    AUTOREACH_ENV: env.AUTOREACH_ENV || "production",
    AUTOREACH_RUNTIME: "cloudflare-container",
    AUTOREACH_EXECUTOR_MODE: "external",
    AUTOREACH_SCHEDULER_ENABLED: "false",
    AUTOREACH_STORAGE_BACKEND: "d1",
    AUTOREACH_LEAD_PIPELINE_BACKEND: "d1",
    AUTOREACH_ARTIFACT_BACKEND: "r2",
    AUTOREACH_EMAIL_WRITER_STORAGE_BACKEND: "d1",
    AUTOREACH_RESEARCH_READER_BACKEND: "d1",
    AUTOREACH_SENDER_STORAGE_BACKEND: "d1",
    AUTOREACH_REPLY_STORAGE_BACKEND: "d1",
    AUTOREACH_REPLY_SOURCE_BACKEND: "d1",
    AUTOREACH_INTERNAL_HMAC_SECRET: env.AUTOREACH_INTERNAL_HMAC_SECRET,
  };
  for (const name of CONTAINER_SECRET_NAMES) {
    const value = env[name];
    if (typeof value === "string" && value) {
      variables[name] = value;
    }
  }
  return variables;
}

async function primaryContainer(env: Env) {
  const container = getContainer(env.AUTOREACH_CONTAINER, PRIMARY_CONTAINER_NAME);
  await container.startAndWaitForPorts({
    ports: 8000,
    startOptions: { envVars: containerEnvironment(env) },
    cancellationOptions: { portReadyTimeoutMS: 30_000 },
  });
  return container;
}

async function signInternalExecution(
  secret: string,
  jobId: string,
  timestamp: string,
): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const payload = `POST\n/internal/jobs/${jobId}/execute\n${timestamp}`;
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(payload),
  );
  return Array.from(new Uint8Array(signature), (value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function executeQueuedJob(jobId: string, attempt: number, env: Env): Promise<Response> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = await signInternalExecution(
    env.AUTOREACH_INTERNAL_HMAC_SECRET,
    jobId,
    timestamp,
  );
  const container = await primaryContainer(env);
  return container.fetch(
    new Request(`https://autoreach.internal/internal/jobs/${encodeURIComponent(jobId)}/execute`, {
      method: "POST",
      headers: {
        "x-autoreach-internal-timestamp": timestamp,
        "x-autoreach-internal-signature": signature,
        "x-autoreach-queue-attempt": attempt.toString(),
      },
    }),
  );
}

function isJobMessage(value: unknown): value is JobMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    "job_id" in value &&
    typeof value.job_id === "string" &&
    value.job_id.length > 0
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/healthz") {
      return (await primaryContainer(env)).fetch(sanitizedRequest(request));
    }
    if (!bearerAuthorized(request, env)) {
      return new Response("Unauthorized", { status: 401 });
    }
    return (await primaryContainer(env)).fetch(sanitizedRequest(request));
  },

  async queue(batch, env): Promise<void> {
    for (const message of batch.messages) {
      if (!isJobMessage(message.body)) {
        message.ack();
        continue;
      }
      try {
        const response = await executeQueuedJob(message.body.job_id, message.attempts, env);
        if (!response.ok) {
          message.retry({ delaySeconds: 30 });
          continue;
        }
        message.ack();
      } catch {
        message.retry({ delaySeconds: 30 });
      }
    }
  },

  async scheduled(_controller, env): Promise<void> {
    // Cron is the durable source of periodic pipeline ticks and outbox recovery.
    await enqueueScheduledTick(env);
    await dispatchPendingJobs(env);
  },
} satisfies ExportedHandler<Env>;
