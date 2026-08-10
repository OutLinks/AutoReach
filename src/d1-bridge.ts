/**
 * Container-only D1 bridge.
 *
 * A Python container reaches this handler through the virtual hostname
 * `http://autoreach.storage`. The container never receives a Cloudflare API
 * token and the bridge deliberately exposes named repository operations rather
 * than arbitrary SQL.
 */

export interface D1BridgeEnv {
  AUTOREACH_DB: D1Database;
  AUTOREACH_JOBS: Queue<{ job_id: string }>;
  AUTOREACH_ARTIFACTS: R2Bucket;
}

/** R2 object bridge for container-produced JSON artifacts. */
export async function handleArtifactBridge(request: Request, env: D1BridgeEnv): Promise<Response> {
  const url = new URL(request.url);
  const key = decodeURIComponent(url.pathname.replace(/^\/v1\/artifacts\//, ""));
  if (!key || key.startsWith("/") || key.includes("..")) {
    return response({ error: "Invalid artifact key" }, 422);
  }
  if (request.method === "PUT") {
    await env.AUTOREACH_ARTIFACTS.put(key, request.body, {
      httpMetadata: { contentType: request.headers.get("content-type") || "application/json" },
    });
    return response({ key }, 201);
  }
  if (request.method === "GET") {
    const object = await env.AUTOREACH_ARTIFACTS.get(key);
    return object ? new Response(object.body, { headers: object.httpMetadata?.contentType ? { "content-type": object.httpMetadata.contentType } : {} }) : response({ error: "Not found" }, 404);
  }
  return response({ error: "Method not allowed" }, 405);
}

type JsonObject = Record<string, unknown>;

function response(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

async function body(request: Request): Promise<JsonObject | null> {
  try {
    const value = await request.json<unknown>();
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as JsonObject)
      : null;
  } catch {
    return null;
  }
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function timestamp(value: unknown): string {
  return typeof value === "string" && value ? value : new Date().toISOString();
}

function jobRow(row: Record<string, unknown> | null): JsonObject | null {
  if (!row) {
    return null;
  }
  return {
    id: row.id,
    kind: row.kind,
    status: row.status,
    payload: JSON.parse(String(row.payload_json || "{}")),
    result: row.result_json ? JSON.parse(String(row.result_json)) : null,
    error: row.error || "",
    dedupe_key: row.dedupe_key || null,
    created_at: row.created_at,
    started_at: row.started_at || null,
    completed_at: row.completed_at || null,
  };
}

async function fetchJob(database: D1Database, jobId: string): Promise<JsonObject | null> {
  const row = await database
    .prepare("SELECT * FROM jobs WHERE id = ?")
    .bind(jobId)
    .first<Record<string, unknown>>();
  return jobRow(row);
}

async function createJob(database: D1Database, input: JsonObject): Promise<Response> {
  const id = nonEmptyString(input.id);
  const kind = nonEmptyString(input.kind);
  if (!id || !kind) {
    return response({ error: "id and kind are required" }, 422);
  }
  const dedupeKey = typeof input.dedupe_key === "string" ? input.dedupe_key : null;
  const createdAt = timestamp(input.created_at);
  const payload = JSON.stringify(input.payload || {});
  try {
    await database.batch([
      database
        .prepare(
          `INSERT INTO jobs (
             id, kind, status, payload_json, dedupe_key, created_at
           ) VALUES (?, ?, 'queued', ?, ?, ?)`,
        )
        .bind(id, kind, payload, dedupeKey, createdAt),
      database
        .prepare("INSERT INTO job_outbox (job_id, created_at) VALUES (?, ?)")
        .bind(id, createdAt),
    ]);
  } catch (error) {
    if (!dedupeKey) {
      throw error;
    }
    const existing = await database
      .prepare("SELECT * FROM jobs WHERE dedupe_key = ?")
      .bind(dedupeKey)
      .first<Record<string, unknown>>();
    if (!existing) {
      throw error;
    }
    return response({ job: jobRow(existing), created: false });
  }
  return response({ job: await fetchJob(database, id), created: true }, 201);
}

async function claimJob(database: D1Database, input: JsonObject): Promise<Response> {
  const id = nonEmptyString(input.id);
  const leaseToken = nonEmptyString(input.lease_token);
  const leaseExpiresAt = nonEmptyString(input.lease_expires_at);
  const executorId = nonEmptyString(input.executor_id);
  if (!id || !leaseToken || !leaseExpiresAt || !executorId) {
    return response({ error: "id, lease_token, lease_expires_at, and executor_id are required" }, 422);
  }
  const startedAt = timestamp(input.started_at);
  const attemptId = crypto.randomUUID();
  const updates = await database.batch([
    database
      .prepare(
        `UPDATE jobs
         SET status = 'running', started_at = ?, error = '', attempt_count = attempt_count + 1,
             lease_token = ?, lease_expires_at = ?
         WHERE id = ? AND status = 'queued'`,
      )
      .bind(startedAt, leaseToken, leaseExpiresAt, id),
    database
      .prepare(
        `INSERT INTO job_attempts (
           id, job_id, attempt_number, executor_id, lease_token, status, started_at
         ) SELECT ?, id, attempt_count, ?, ?, 'running', ?
           FROM jobs WHERE id = ? AND status = 'running' AND lease_token = ?`,
      )
      .bind(attemptId, executorId, leaseToken, startedAt, id, leaseToken),
  ]);
  if ((updates[0].meta.changes || 0) !== 1) {
    return response({ job: null, claimed: false });
  }
  return response({ job: await fetchJob(database, id), claimed: true });
}

async function markDispatched(database: D1Database, jobId: string): Promise<void> {
  await database
    .prepare("UPDATE job_outbox SET dispatched_at = ?, retry_after = NULL WHERE job_id = ?")
    .bind(new Date().toISOString(), jobId)
    .run();
}

async function dispatchJob(env: D1BridgeEnv, jobId: string): Promise<boolean> {
  try {
    await env.AUTOREACH_JOBS.send({ job_id: jobId });
    await markDispatched(env.AUTOREACH_DB, jobId);
    return true;
  } catch (error) {
    console.error("Queue dispatch failed; leaving durable outbox entry", error);
    await env.AUTOREACH_DB
      .prepare("UPDATE job_outbox SET retry_after = ? WHERE job_id = ?")
      .bind(new Date(Date.now() + 30_000).toISOString(), jobId)
      .run();
    return false;
  }
}

/** Dispatch persisted jobs whose initial Queue publish did not succeed. */
export async function dispatchPendingJobs(env: D1BridgeEnv): Promise<number> {
  // A stopped container leaves a durable running row. Reclaim it only after
  // the execution lease expires, never while an active attempt may still send.
  await recoverJobs(env.AUTOREACH_DB);
  const now = new Date().toISOString();
  const rows = await env.AUTOREACH_DB
    .prepare(
      `SELECT job_id FROM job_outbox
       WHERE dispatched_at IS NULL AND (retry_after IS NULL OR retry_after <= ?)
       ORDER BY created_at
       LIMIT 100`,
    )
    .bind(now)
    .all<{ job_id: string }>();
  let dispatched = 0;
  for (const row of rows.results) {
    if (await dispatchJob(env, row.job_id)) {
      dispatched += 1;
    }
  }
  return dispatched;
}

async function completeJob(
  database: D1Database,
  input: JsonObject,
  status: "succeeded" | "failed",
): Promise<Response> {
  const id = nonEmptyString(input.id);
  const leaseToken = nonEmptyString(input.lease_token);
  if (!id || !leaseToken) {
    return response({ error: "id and lease_token are required" }, 422);
  }
  const completedAt = timestamp(input.completed_at);
  const result = status === "succeeded" ? JSON.stringify(input.result ?? null) : null;
  const error = status === "failed" ? String(input.error || "") .slice(0, 4000) : "";
  const updates = await database.batch([
    database
      .prepare(
        `UPDATE jobs
         SET status = ?, result_json = ?, error = ?, completed_at = ?,
             lease_token = NULL, lease_expires_at = NULL
         WHERE id = ? AND status = 'running' AND lease_token = ?`,
      )
      .bind(status, result, error, completedAt, id, leaseToken),
    database
      .prepare(
        `UPDATE job_attempts SET status = ?, error = ?, completed_at = ?
         WHERE job_id = ? AND lease_token = ? AND status = 'running'
           AND EXISTS (
             SELECT 1 FROM jobs
             WHERE id = ? AND status = ? AND completed_at = ? AND lease_token IS NULL
           )`,
      )
      .bind(status, error, completedAt, id, leaseToken, id, status, completedAt),
  ]);
  if ((updates[0].meta.changes || 0) !== 1) {
    return response({ error: "Job was not claimed by this execution" }, 409);
  }
  return response({ job: await fetchJob(database, id) });
}

async function requeueFailedJob(database: D1Database, input: JsonObject): Promise<Response> {
  const id = nonEmptyString(input.id);
  if (!id) {
    return response({ error: "id is required" }, 422);
  }
  const update = await database
    .prepare(
      `UPDATE jobs
       SET status = 'queued', started_at = NULL, completed_at = NULL,
           lease_token = NULL, lease_expires_at = NULL
       WHERE id = ? AND status = 'failed'`,
    )
    .bind(id)
    .run();
  if ((update.meta.changes || 0) !== 1) {
    return response({ job: null, requeued: false });
  }
  return response({ job: await fetchJob(database, id), requeued: true });
}

async function recoverJobs(database: D1Database): Promise<Response> {
  const now = new Date().toISOString();
  await database.batch([
    database
      .prepare(
        `UPDATE jobs
         SET status = 'queued', started_at = NULL, lease_token = NULL,
             lease_expires_at = NULL, error = 'Recovered after lease expiry'
         WHERE status = 'running' AND lease_expires_at <= ?`,
      )
      .bind(now),
    database
      .prepare(
        `UPDATE job_outbox SET dispatched_at = NULL, retry_after = NULL
         WHERE job_id IN (
           SELECT id FROM jobs WHERE status = 'queued' AND error = 'Recovered after lease expiry'
         )`,
      ),
  ]);
  const rows = await database
    .prepare("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at")
    .all<{ id: string }>();
  return response({ job_ids: rows.results.map((row) => row.id) });
}

/** Handle a named JobRepository operation from the Python container. */
export async function handleD1Bridge(request: Request, env: D1BridgeEnv): Promise<Response> {
  if (request.method !== "POST") {
    return response({ error: "Method not allowed" }, 405);
  }
  const action = new URL(request.url).pathname.replace(/^\/v1\/jobs\//, "");
  const input = await body(request);
  if (!input) {
    return response({ error: "Expected JSON object" }, 400);
  }
  try {
    if (action === "create") {
      const created = await createJob(env.AUTOREACH_DB, input);
      if (created.status === 201) {
        const data = (await created.clone().json<{ job: { id: string } }>()).job;
        await dispatchJob(env, data.id);
      }
      return created;
    }
    if (action === "get") {
      const id = nonEmptyString(input.id);
      const job = id ? await fetchJob(env.AUTOREACH_DB, id) : null;
      return job ? response({ job }) : response({ error: "Job not found" }, 404);
    }
    if (action === "list") {
      const limit = Math.min(Math.max(Number(input.limit) || 50, 1), 200);
      const offset = Math.max(Number(input.offset) || 0, 0);
      const rows = await env.AUTOREACH_DB
        .prepare("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?")
        .bind(limit, offset)
        .all<Record<string, unknown>>();
      return response({ jobs: rows.results.map(jobRow) });
    }
    if (action === "claim") return await claimJob(env.AUTOREACH_DB, input);
    if (action === "succeed") return await completeJob(env.AUTOREACH_DB, input, "succeeded");
    if (action === "fail") return await completeJob(env.AUTOREACH_DB, input, "failed");
    if (action === "retry") return await requeueFailedJob(env.AUTOREACH_DB, input);
    if (action === "recover") return await recoverJobs(env.AUTOREACH_DB);
    return response({ error: "Unknown job operation" }, 404);
  } catch (error) {
    console.error("D1 storage bridge failed", error);
    return response({ error: "D1 storage operation failed" }, 500);
  }
}

/** Durable replacement for Agent 1's Redis job lists and global seen sets. */
export async function handleLeadPipelineBridge(request: Request, env: D1BridgeEnv): Promise<Response> {
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/lead-pipeline\//, "");
  const input = await body(request);
  if (!input) return response({ error: "Expected JSON object" }, 400);
  const jobId = nonEmptyString(input.job_id);
  try {
    if (operation === "save-job" && jobId) {
      const expiresAt = nonEmptyString(input.expires_at);
      if (!expiresAt) return response({ error: "expires_at is required" }, 422);
      await env.AUTOREACH_DB
        .prepare(
          `INSERT INTO lead_pipeline_jobs (id, job_json, expires_at, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET job_json = excluded.job_json,
             expires_at = excluded.expires_at, updated_at = excluded.updated_at`,
        )
        .bind(jobId, JSON.stringify(input.job || {}), expiresAt, new Date().toISOString())
        .run();
      return response({ saved: true });
    }
    if (operation === "load-job" && jobId) {
      const row = await env.AUTOREACH_DB
        .prepare("SELECT job_json FROM lead_pipeline_jobs WHERE id = ? AND expires_at > ?")
        .bind(jobId, new Date().toISOString())
        .first<{ job_json: string }>();
      return response({ job: row ? JSON.parse(row.job_json) : null });
    }
    if (operation === "push-leads" && jobId) {
      const stage = nonEmptyString(input.stage);
      const expiresAt = nonEmptyString(input.expires_at);
      const leads = Array.isArray(input.leads) ? input.leads : [];
      if (!stage || !expiresAt) return response({ error: "stage and expires_at are required" }, 422);
      const max = await env.AUTOREACH_DB
        .prepare("SELECT COALESCE(MAX(ordinal), -1) AS value FROM lead_stage_records WHERE job_id = ? AND stage = ?")
        .bind(jobId, stage)
        .first<{ value: number }>();
      const start = (max?.value || -1) + 1;
      await env.AUTOREACH_DB.batch(
        leads.map((lead, index) => env.AUTOREACH_DB
          .prepare("INSERT INTO lead_stage_records (job_id, stage, ordinal, lead_json, expires_at) VALUES (?, ?, ?, ?, ?)")
          .bind(jobId, stage, start + index, JSON.stringify(lead), expiresAt)),
      );
      return response({ pushed: leads.length });
    }
    if (operation === "pull-leads" && jobId) {
      const stage = nonEmptyString(input.stage);
      if (!stage) return response({ error: "stage is required" }, 422);
      const rows = await env.AUTOREACH_DB
        .prepare(`SELECT lead_json FROM lead_stage_records
                  WHERE job_id = ? AND stage = ? AND expires_at > ? ORDER BY ordinal`)
        .bind(jobId, stage, new Date().toISOString())
        .all<{ lead_json: string }>();
      return response({ leads: rows.results.map((row) => JSON.parse(row.lead_json)) });
    }
    if (operation === "list-leads") {
      const leadIds = Array.isArray(input.lead_ids)
        ? input.lead_ids.filter((value): value is string => typeof value === "string" && value.length > 0)
        : [];
      const where = leadIds.length ? `WHERE id IN (${leadIds.map(() => "?").join(",")})` : "";
      const rows = await env.AUTOREACH_DB.prepare(
        `SELECT id, metadata_json FROM leads ${where} ORDER BY updated_at DESC`,
      ).bind(...leadIds).all<{ id: string; metadata_json: string }>();
      const wanted = new Set(leadIds);
      const seen = new Set<string>();
      const leads = rows.results
        .map((item) => ({ ...JSON.parse(item.metadata_json || "{}") as JsonObject, id: item.id }))
        .filter((lead) => {
          const id = nonEmptyString(lead.id);
          if (!id || seen.has(id) || (wanted.size && !wanted.has(id))) return false;
          seen.add(id);
          return true;
        });
      return response({ leads });
    }
    if (operation === "persist-final-leads") {
      const leads = Array.isArray(input.leads) ? input.leads : [];
      const statements = leads.flatMap((value) => {
        if (!value || typeof value !== "object") return [];
        const lead = value as JsonObject;
        const id = nonEmptyString(lead.id);
        if (!id || Boolean(lead.is_duplicate)) return [];
        const createdAt = timestamp(lead.created_at);
        const updatedAt = timestamp(lead.updated_at);
        return [env.AUTOREACH_DB.prepare(
          `INSERT INTO leads (id,state,email,company,industry,quality_score,priority,source_job,metadata_json,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
           state=excluded.state,email=excluded.email,company=excluded.company,industry=excluded.industry,
           quality_score=excluded.quality_score,priority=excluded.priority,source_job=excluded.source_job,
           metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`,
        ).bind(id, "qualified", lead.email || null, lead.company_name || null, lead.industry || null,
          Number(lead.lead_score || 0), Number(lead.lead_score || 0), input.job_id || "",
          JSON.stringify(lead), createdAt, updatedAt)];
      });
      if (statements.length) await env.AUTOREACH_DB.batch(statements);
      return response({ persisted: statements.length });
    }
    if (operation === "is-duplicate" || operation === "register-lead") {
      const keys = Array.isArray(input.keys) ? input.keys : [];
      if (operation === "is-duplicate") {
        for (const key of keys) {
          if (!key || typeof key !== "object") continue;
          const item = key as { type?: unknown; hash?: unknown };
          if (typeof item.type !== "string" || typeof item.hash !== "string") continue;
          const found = await env.AUTOREACH_DB
            .prepare("SELECT 1 FROM lead_dedup_keys WHERE key_type = ? AND value_hash = ?")
            .bind(item.type, item.hash)
            .first();
          if (found) return response({ duplicate: true });
        }
        return response({ duplicate: false });
      }
      const statements = keys.flatMap((key) => {
        if (!key || typeof key !== "object") return [];
        const item = key as { type?: unknown; hash?: unknown };
        return typeof item.type === "string" && typeof item.hash === "string"
          ? [env.AUTOREACH_DB.prepare("INSERT OR IGNORE INTO lead_dedup_keys (key_type, value_hash, created_at) VALUES (?, ?, ?)")
            .bind(item.type, item.hash, new Date().toISOString())]
          : [];
      });
      if (statements.length) await env.AUTOREACH_DB.batch(statements);
      return response({ registered: statements.length });
    }
    if (operation === "clear-job" && jobId) {
      await env.AUTOREACH_DB.batch([
        env.AUTOREACH_DB.prepare("DELETE FROM lead_stage_records WHERE job_id = ?").bind(jobId),
        env.AUTOREACH_DB.prepare("DELETE FROM lead_pipeline_jobs WHERE id = ?").bind(jobId),
      ]);
      return response({ cleared: true });
    }
    return response({ error: "Unknown lead pipeline operation" }, 404);
  } catch (error) {
    console.error("D1 lead pipeline bridge failed", error);
    return response({ error: "D1 lead pipeline operation failed" }, 500);
  }
}

/** Durable Agent 3 written-email records. The operations deliberately match
 * the repository methods exposed to the Python container. */
export async function handleEmailWriterBridge(request: Request, env: D1BridgeEnv): Promise<Response> {
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/email-writer\//, "");
  const input = await body(request);
  if (!input) return response({ error: "Expected JSON object" }, 400);
  const obj = (name: string) => (
    input[name] && typeof input[name] === "object" ? input[name] as JsonObject : null
  );
  const row = (value: Record<string, unknown> | null): JsonObject | null => {
    if (!value) return null;
    return {
      ...value,
      quality_passed: Boolean(value.quality_passed),
      quality_report: value.quality_report_json ? JSON.parse(String(value.quality_report_json)) : null,
    };
  };
  const now = new Date().toISOString();

  try {
    if (operation === "upsert-email") {
      const email = obj("email");
      if (!email || !nonEmptyString(email.id) || !nonEmptyString(email.lead_id)) {
        return response({ error: "email id and lead_id are required" }, 422);
      }
      await env.AUTOREACH_DB.prepare(
        `INSERT INTO written_emails (
          id,lead_id,research_profile_id,subject,body,hook,cta,
          lead_first_name,lead_last_name,lead_company,recipient,timezone,city,state,country,
          sender_name,sender_email,tone,template_name,quality_score,quality_passed,
          quality_report_json,status,job_id,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          subject=excluded.subject,body=excluded.body,hook=excluded.hook,cta=excluded.cta,
          recipient=excluded.recipient,timezone=excluded.timezone,city=excluded.city,
          state=excluded.state,country=excluded.country,quality_score=excluded.quality_score,
          quality_passed=excluded.quality_passed,quality_report_json=excluded.quality_report_json,
          status=excluded.status,job_id=excluded.job_id`,
      ).bind(
        email.id, email.lead_id, email.research_profile_id || "", email.subject || "", email.body || "",
        email.hook || "", email.cta || "", email.lead_first_name || "", email.lead_last_name || "",
        email.lead_company || "", email.recipient || "", email.timezone || "", email.city || "",
        email.state || "", email.country || "", email.sender_name || "", email.sender_email || "",
        email.tone || "", email.template_name || "", Number(email.quality_score || 0),
        Number(Boolean(email.quality_passed)), email.quality_report ? JSON.stringify(email.quality_report) : null,
        email.status || "draft", email.job_id || "", email.created_at || now,
      ).run();
      return response({ saved: true });
    }

    if (operation === "get-email") {
      const email = await env.AUTOREACH_DB.prepare("SELECT * FROM written_emails WHERE id = ?")
        .bind(input.id || "").first<Record<string, unknown>>();
      return response({ email: row(email) });
    }

    if (operation === "list-sendable") {
      const statuses = Array.isArray(input.statuses)
        ? input.statuses.filter((value): value is string => typeof value === "string" && value.length > 0)
        : ["approved"];
      const leadIds = Array.isArray(input.lead_ids)
        ? input.lead_ids.filter((value): value is string => typeof value === "string" && value.length > 0)
        : [];
      if (!statuses.length) return response({ items: [] });
      const where = [`status IN (${statuses.map(() => "?").join(",")})`];
      const values: unknown[] = [...statuses];
      if (leadIds.length) {
        where.push(`lead_id IN (${leadIds.map(() => "?").join(",")})`);
        values.push(...leadIds);
      }
      const emails = await env.AUTOREACH_DB.prepare(
        `SELECT * FROM written_emails WHERE ${where.join(" AND ")} ORDER BY created_at`,
      ).bind(...values).all<Record<string, unknown>>();
      return response({ items: emails.results.map(row) });
    }

    if (operation === "list-by-job" || operation === "list-by-lead") {
      const field = operation === "list-by-job" ? "job_id" : "lead_id";
      const value = field === "job_id" ? input.job_id : input.lead_id;
      const emails = await env.AUTOREACH_DB.prepare(
        `SELECT * FROM written_emails WHERE ${field} = ? ORDER BY created_at ${field === "lead_id" ? "DESC" : ""}`,
      ).bind(value || "").all<Record<string, unknown>>();
      return response({ items: emails.results.map(row) });
    }

    if (operation === "update-status") {
      const id = nonEmptyString(input.id);
      const status = nonEmptyString(input.status);
      if (!id || !status) return response({ error: "id and status are required" }, 422);
      await env.AUTOREACH_DB.prepare("UPDATE written_emails SET status = ? WHERE id = ?")
        .bind(status, id).run();
      return response({ updated: true });
    }

    if (operation === "status-counts") {
      const jobId = nonEmptyString(input.job_id);
      const statement = jobId
        ? env.AUTOREACH_DB.prepare("SELECT status, COUNT(*) AS count FROM written_emails WHERE job_id = ? GROUP BY status").bind(jobId)
        : env.AUTOREACH_DB.prepare("SELECT status, COUNT(*) AS count FROM written_emails GROUP BY status");
      const counts = await statement.all<{ status: string; count: number }>();
      return response({ counts: Object.fromEntries(counts.results.map((item) => [item.status, item.count])) });
    }

    if (operation === "upsert-job") {
      const job = obj("job");
      if (!job || !nonEmptyString(job.id)) return response({ error: "job is required" }, 422);
      await env.AUTOREACH_DB.prepare(
        `INSERT INTO email_writer_jobs (id,status,total,written,quality_passed,quality_failed,skipped,created_at,completed_at)
         VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
         status=excluded.status,total=excluded.total,written=excluded.written,
         quality_passed=excluded.quality_passed,quality_failed=excluded.quality_failed,
         skipped=excluded.skipped,completed_at=excluded.completed_at`,
      ).bind(job.id, job.status || "pending", Number(job.total || 0), Number(job.written || 0),
        Number(job.quality_passed || 0), Number(job.quality_failed || 0), Number(job.skipped || 0),
        job.created_at || now, job.completed_at || null).run();
      return response({ saved: true });
    }
    return response({ error: "Unsupported email-writer operation" }, 404);
  } catch (error) {
    console.error("D1 email-writer bridge failed", error);
    return response({ error: "D1 email-writer storage operation failed" }, 500);
  }
}

/** Indexes R2-held Agent 2 profiles so Agent 3 can select them durably. */
export async function handleResearchIndexBridge(request: Request, env: D1BridgeEnv): Promise<Response> {
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/research-index\//, "");
  const input = await body(request);
  if (!input) return response({ error: "Expected JSON object" }, 400);
  const now = new Date().toISOString();
  try {
    if (operation === "upsert-profile") {
      const id = nonEmptyString(input.id);
      const leadId = nonEmptyString(input.lead_id);
      const jobId = nonEmptyString(input.job_id);
      const artifactKey = nonEmptyString(input.artifact_key);
      if (!id || !leadId || !jobId || !artifactKey) {
        return response({ error: "id, lead_id, job_id, and artifact_key are required" }, 422);
      }
      await env.AUTOREACH_DB.prepare(
        `INSERT INTO research_profile_index (id,lead_id,job_id,artifact_key,status,quality_score,updated_at)
         VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET lead_id=excluded.lead_id,
         job_id=excluded.job_id,artifact_key=excluded.artifact_key,status=excluded.status,
         quality_score=excluded.quality_score,updated_at=excluded.updated_at`,
      ).bind(id, leadId, jobId, artifactKey, input.status || "partial", Number(input.quality_score || 0), now).run();
      return response({ saved: true });
    }
    if (operation === "list-profiles") {
      const statuses = Array.isArray(input.statuses)
        ? input.statuses.filter((value): value is string => typeof value === "string" && value.length > 0)
        : ["complete", "partial"];
      const leadIds = Array.isArray(input.lead_ids)
        ? input.lead_ids.filter((value): value is string => typeof value === "string" && value.length > 0)
        : [];
      if (!statuses.length) return response({ items: [] });
      const clauses = [`status IN (${statuses.map(() => "?").join(",")})`, "quality_score >= ?"];
      const values: unknown[] = [...statuses, Number(input.min_quality_score || 0)];
      if (leadIds.length) {
        clauses.push(`lead_id IN (${leadIds.map(() => "?").join(",")})`);
        values.push(...leadIds);
      }
      const rows = await env.AUTOREACH_DB.prepare(
        `SELECT * FROM research_profile_index WHERE ${clauses.join(" AND ")} ORDER BY updated_at`,
      ).bind(...values).all<Record<string, unknown>>();
      return response({ items: rows.results });
    }
    return response({ error: "Unsupported research-index operation" }, 404);
  } catch (error) {
    console.error("D1 research index bridge failed", error);
    return response({ error: "D1 research index operation failed" }, 500);
  }
}

/** Durable Agent 4 sender state. This bridge deliberately exposes operations,
 * not SQL, to the Python container. */
export async function handleSenderBridge(request: Request, env: D1BridgeEnv): Promise<Response> {
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/sender\//, "");
  const input = await body(request);
  if (!input) return response({ error: "Expected JSON object" }, 400);
  const now = new Date().toISOString();
  const obj = (name: string) => (input[name] && typeof input[name] === "object" ? input[name] as JsonObject : null);
  try {
    if (operation === "upsert-sent") {
      const sent = obj("sent"); if (!sent || !nonEmptyString(sent.id)) return response({ error: "sent is required" }, 422);
      await env.AUTOREACH_DB.prepare(`INSERT INTO sent_emails (id,email_id,lead_id,step,recipient,account_email,provider,message_id,idempotency_key,subject,body,status,opened,clicked,replied,bounced,sent_at,job_id,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET message_id=excluded.message_id,status=excluded.status,opened=excluded.opened,clicked=excluded.clicked,replied=excluded.replied,bounced=excluded.bounced,sent_at=excluded.sent_at`)
        .bind(sent.id, sent.email_id || "", sent.lead_id || "", sent.step || "", sent.recipient || "", sent.account_email || "", sent.provider || "smtp", sent.message_id || "", sent.idempotency_key || null, sent.subject || "", sent.body || "", sent.status || "queued", Number(Boolean(sent.opened)), Number(Boolean(sent.clicked)), Number(Boolean(sent.replied)), Number(Boolean(sent.bounced)), sent.sent_at || null, sent.job_id || "", sent.created_at || now).run();
      return response({ saved: true });
    }
    if (operation === "get-sent" || operation === "get-by-message-id" || operation === "get-by-idempotency" || operation === "latest-sent") {
      const sql = operation === "get-sent" ? "SELECT * FROM sent_emails WHERE id = ?" : operation === "get-by-message-id" ? "SELECT * FROM sent_emails WHERE message_id = ?" : operation === "get-by-idempotency" ? "SELECT * FROM sent_emails WHERE idempotency_key = ?" : "SELECT * FROM sent_emails WHERE lead_id = ? ORDER BY created_at DESC LIMIT 1";
      const key = operation === "get-sent" ? input.id : operation === "get-by-message-id" ? input.message_id : operation === "get-by-idempotency" ? input.idempotency_key : input.lead_id;
      const sent = await env.AUTOREACH_DB.prepare(sql).bind(key || "").first<Record<string, unknown>>(); return response({ sent });
    }
    if (operation === "update-sent") {
      const flags = obj("flags") || {}; const id = nonEmptyString(input.id); if (!id) return response({ error: "id is required" }, 422);
      await env.AUTOREACH_DB.prepare("UPDATE sent_emails SET status=?, opened=MAX(opened,?), clicked=MAX(clicked,?), replied=MAX(replied,?), bounced=MAX(bounced,?) WHERE id=?").bind(input.status || "queued", Number(Boolean(flags.opened)), Number(Boolean(flags.clicked)), Number(Boolean(flags.replied)), Number(Boolean(flags.bounced)), id).run(); return response({ updated: true });
    }
    if (operation === "list-sent") { const status = nonEmptyString(input.status); const rows = await env.AUTOREACH_DB.prepare(status ? "SELECT * FROM sent_emails WHERE status=? ORDER BY created_at" : "SELECT * FROM sent_emails ORDER BY created_at").bind(...(status ? [status] : [])).all<Record<string, unknown>>(); return response({ items: rows.results }); }
    if (operation === "upsert-sequence") {
      const s = obj("sequence"); if (!s || !nonEmptyString(s.lead_id)) return response({ error: "sequence is required" }, 422);
      await env.AUTOREACH_DB.prepare(`INSERT INTO sequence_states (lead_id,email_id,current_step,status,steps_sent_json,next_send_at_utc,initial_sent_at,recipient,account_email,timezone,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET email_id=excluded.email_id,current_step=excluded.current_step,status=excluded.status,steps_sent_json=excluded.steps_sent_json,next_send_at_utc=excluded.next_send_at_utc,updated_at=excluded.updated_at`).bind(s.lead_id,s.email_id||"",s.current_step||"",s.status||"active",JSON.stringify(s.steps_sent||[]),s.next_send_at||null,s.initial_sent_at||null,s.recipient||"",s.account_email||"",s.timezone||"UTC",s.created_at||now,s.updated_at||now).run(); return response({ saved:true });
    }
    if (operation === "get-sequence" || operation === "active-sequences") { const rows = await env.AUTOREACH_DB.prepare(operation === "get-sequence" ? "SELECT lead_id,email_id,current_step,status,steps_sent_json AS steps_sent,next_send_at_utc AS next_send_at,initial_sent_at,recipient,account_email,timezone,created_at,updated_at FROM sequence_states WHERE lead_id=?" : "SELECT lead_id,email_id,current_step,status,steps_sent_json AS steps_sent,next_send_at_utc AS next_send_at,initial_sent_at,recipient,account_email,timezone,created_at,updated_at FROM sequence_states WHERE status='active'").bind(...(operation === "get-sequence" ? [input.lead_id || ""] : [])).all<Record<string, unknown>>(); const values=rows.results.map((r)=>({...r,steps_sent:JSON.parse(String(r.steps_sent||"[]"))})); return response(operation === "get-sequence" ? { sequence: values[0] || null } : { items: values }); }
    if (operation === "upsert-account") { const a=obj("account"); if (!a || !nonEmptyString(a.email)) return response({error:"account is required"},422); await env.AUTOREACH_DB.prepare("INSERT INTO sending_accounts (email,provider,display_name,daily_limit,hourly_limit,sent_today,sent_this_hour,health_score,status,warmup_start_date) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET provider=excluded.provider,display_name=excluded.display_name,daily_limit=excluded.daily_limit,hourly_limit=excluded.hourly_limit,status=excluded.status").bind(a.email,a.provider||"smtp",a.display_name||"",a.daily_limit||50,a.hourly_limit||10,a.sent_today||0,a.sent_this_hour||0,a.health_score??1,a.status||"active",a.warmup_start_date||null).run(); return response({saved:true}); }
    if (operation === "list-accounts") { const rows=await env.AUTOREACH_DB.prepare("SELECT * FROM sending_accounts").all<Record<string,unknown>>(); return response({items:rows.results}); }
    if (operation === "insert-event") { const e=obj("event"); if (!e || !nonEmptyString(e.id)) return response({error:"event is required"},422); await env.AUTOREACH_DB.prepare("INSERT OR IGNORE INTO tracking_events (id,sent_email_id,lead_id,event_type,detail,bounce_type,occurred_at,metadata_json) VALUES (?,?,?,?,?,?,?,?)").bind(e.id,e.sent_email_id||"",e.lead_id||"",e.event_type||"",e.detail||"",e.bounce_type||"",e.occurred_at||now,JSON.stringify(e.metadata||{})).run(); return response({saved:true}); }
    if (operation === "get-events") { const rows=await env.AUTOREACH_DB.prepare("SELECT id,sent_email_id,lead_id,event_type,detail,bounce_type,occurred_at,metadata_json AS metadata FROM tracking_events WHERE sent_email_id=? ORDER BY occurred_at").bind(input.sent_email_id||"").all<Record<string,unknown>>(); return response({items:rows.results.map((r)=>({...r,metadata:JSON.parse(String(r.metadata||"{}"))}))}); }
    if (operation === "event-counts") { const rows=await env.AUTOREACH_DB.prepare("SELECT event_type, COUNT(*) count FROM tracking_events GROUP BY event_type").all<{event_type:string;count:number}>(); return response({counts:Object.fromEntries(rows.results.map((r)=>[r.event_type,r.count]))}); }
    if (operation === "upsert-suppression") { const e=obj("entry"); if (!e || !nonEmptyString(e.value)) return response({error:"entry is required"},422); await env.AUTOREACH_DB.prepare("INSERT INTO suppression_list (value,is_domain,reason,detail,added_at) VALUES (?,?,?,?,?) ON CONFLICT(value) DO UPDATE SET is_domain=excluded.is_domain,reason=excluded.reason,detail=excluded.detail").bind(String(e.value).toLowerCase(),Number(Boolean(e.is_domain)),e.reason||"",e.detail||"",e.added_at||now).run(); return response({saved:true}); }
    if (operation === "is-suppressed") { const email=String(input.email||"").toLowerCase(); const domain=email.includes("@") ? email.split("@").pop() : ""; const found=await env.AUTOREACH_DB.prepare("SELECT 1 FROM suppression_list WHERE (is_domain=0 AND value=?) OR (is_domain=1 AND value=?) LIMIT 1").bind(email,domain).first(); return response({suppressed:Boolean(found)}); }
    if (operation === "list-suppressions") { const rows=await env.AUTOREACH_DB.prepare("SELECT * FROM suppression_list").all<Record<string,unknown>>(); return response({items:rows.results}); }
    if (operation === "upsert-job") { const j=obj("job"); if (!j || !nonEmptyString(j.id)) return response({error:"job is required"},422); await env.AUTOREACH_DB.prepare("INSERT INTO send_jobs (id,kind,status,total,sent,skipped,failed,suppressed,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,total=excluded.total,sent=excluded.sent,skipped=excluded.skipped,failed=excluded.failed,suppressed=excluded.suppressed,completed_at=excluded.completed_at").bind(j.id,j.kind||"",j.status||"",j.total||0,j.sent||0,j.skipped||0,j.failed||0,j.suppressed||0,j.created_at||now,j.completed_at||null).run(); return response({saved:true}); }
    if (operation === "metrics") { const account=nonEmptyString(input.account_email); const row=await env.AUTOREACH_DB.prepare(`SELECT COUNT(*) sent, SUM(CASE WHEN status IN ('delivered','opened','clicked','replied') THEN 1 ELSE 0 END) delivered, SUM(bounced) bounced, SUM(CASE WHEN status='complained' THEN 1 ELSE 0 END) complained, SUM(opened) opened FROM sent_emails ${account && account !== '*' ? 'WHERE account_email=?' : ''}`).bind(...(account && account !== '*' ? [account] : [])).first<Record<string,unknown>>(); return response({metrics:{sent:Number(row?.sent||0),delivered:Number(row?.delivered||0),bounced:Number(row?.bounced||0),complained:Number(row?.complained||0),opened:Number(row?.opened||0)}}); }
    return response({ error: "Unsupported sender operation" }, 404);
  } catch (error) { console.error("D1 sender bridge failed", error); return response({ error: "D1 sender operation failed" }, 500); }
}
