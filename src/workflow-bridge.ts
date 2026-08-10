type JsonObject = Record<string, unknown>;

interface WorkflowBridgeEnv {
  AUTOREACH_DB: D1Database;
}

const reply = (value: unknown, status = 200) => Response.json(value, { status });
const string = (value: unknown) => typeof value === "string" ? value : "";
const object = (value: unknown): JsonObject => (
  value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {}
);

async function inputBody(request: Request): Promise<JsonObject | null> {
  try {
    const value = await request.json<unknown>();
    return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
  } catch {
    return null;
  }
}

function researchRow(row: Record<string, unknown> | null): JsonObject | null {
  return row ? {
    id: row.id, lead_id: row.lead_id, company: row.company, summary: row.summary,
    sections: JSON.parse(String(row.sections_json || "[]")),
    sources: JSON.parse(String(row.sources_json || "[]")),
    version: row.version, updated_at: row.updated_at,
  } : null;
}

function draftRow(row: Record<string, unknown> | null, internal = false): JsonObject | null {
  if (!row) return null;
  if (internal) return { ...row };
  return {
    id: row.id, lead_id: row.lead_id, campaign_id: row.campaign_id,
    subject: row.subject, body: row.body, status: row.status,
    version: row.version, updated_at: row.updated_at,
  };
}

function searchSummary(row: Record<string, unknown> | null): JsonObject | null {
  return row ? {
    id: row.id, query: row.query, status: row.status,
    found_count: row.found_count, created_at: row.created_at,
  } : null;
}

async function fetchResearch(db: D1Database, id: unknown) {
  return researchRow(await db.prepare("SELECT * FROM research_documents WHERE id = ?")
    .bind(id || "").first<Record<string, unknown>>());
}

async function fetchDraft(db: D1Database, id: unknown, internal = false) {
  return draftRow(await db.prepare("SELECT * FROM email_drafts WHERE id = ?")
    .bind(id || "").first<Record<string, unknown>>(), internal);
}

async function fetchSearch(db: D1Database, id: unknown): Promise<JsonObject | null> {
  const row = await db.prepare("SELECT * FROM lead_searches WHERE id = ?")
    .bind(id || "").first<Record<string, unknown>>();
  if (!row) return null;
  const results = await db.prepare(
    "SELECT payload_json,imported FROM lead_search_results WHERE search_id = ? ORDER BY rowid",
  ).bind(id || "").all<{ payload_json: string; imported: number }>();
  return {
    ...searchSummary(row),
    filters: JSON.parse(String(row.filters_json || "{}")),
    error: row.error,
    leads: results.results.map((item) => ({
      ...JSON.parse(item.payload_json), imported: Boolean(item.imported),
    })),
  };
}

export async function handleWorkflowBridge(request: Request, env: WorkflowBridgeEnv): Promise<Response> {
  if (request.method !== "POST") return reply({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/workflows\//, "");
  const input = await inputBody(request);
  if (!input) return reply({ error: "Expected JSON object" }, 400);
  const db = env.AUTOREACH_DB;

  try {
    if (operation === "create-research") {
      await db.prepare(
        `INSERT INTO research_documents (id,lead_id,company,prompt,summary,sections_json,sources_json,version,created_at,updated_at)
         VALUES (?,?,?,?,?,?,?,1,?,?)`,
      ).bind(input.id, input.lead_id || null, input.company || "", input.prompt || "", input.summary || "",
        JSON.stringify(input.sections || []), JSON.stringify(input.sources || []), input.now, input.now).run();
      return reply({ item: await fetchResearch(db, input.id) });
    }
    if (operation === "list-research") {
      const leadId = string(input.lead_id);
      const rows = await db.prepare(leadId
        ? "SELECT * FROM research_documents WHERE lead_id = ? ORDER BY updated_at DESC"
        : "SELECT * FROM research_documents ORDER BY updated_at DESC")
        .bind(...(leadId ? [leadId] : [])).all<Record<string, unknown>>();
      return reply({ items: rows.results.map(researchRow) });
    }
    if (operation === "get-research") return reply({ item: await fetchResearch(db, input.id) });
    if (operation === "update-research") {
      const values = object(input.values);
      const current = await db.prepare("SELECT * FROM research_documents WHERE id = ?")
        .bind(input.id || "").first<Record<string, unknown>>();
      if (!current) return reply({ item: null });
      await db.prepare(
        `UPDATE research_documents SET lead_id=?,company=?,summary=?,sections_json=?,sources_json=?,
         version=version+1,updated_at=? WHERE id=?`,
      ).bind(values.lead_id ?? current.lead_id, values.company ?? current.company,
        values.summary ?? current.summary,
        values.sections !== undefined ? JSON.stringify(values.sections) : current.sections_json,
        values.sources !== undefined ? JSON.stringify(values.sources) : current.sources_json,
        input.now, input.id).run();
      return reply({ item: await fetchResearch(db, input.id) });
    }

    if (operation === "create-draft") {
      await db.prepare(
        `INSERT INTO email_drafts (id,lead_id,campaign_id,subject,body,status,version,tone,instructions,source_email_id,created_at,updated_at)
         VALUES (?,?,?,?,?,'draft',1,?,?,?,?,?)`,
      ).bind(input.id, input.lead_id, input.campaign_id, input.subject || "", input.body || "",
        input.tone || "", input.instructions || "", input.source_email_id || "", input.now, input.now).run();
      return reply({ item: await fetchDraft(db, input.id) });
    }
    if (operation === "list-drafts") {
      const clauses: string[] = [];
      const values: unknown[] = [];
      for (const field of ["lead_id", "campaign_id", "status"] as const) {
        if (string(input[field])) { clauses.push(`${field} = ?`); values.push(input[field]); }
      }
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      const rows = await db.prepare(`SELECT * FROM email_drafts ${where} ORDER BY updated_at DESC`)
        .bind(...values).all<Record<string, unknown>>();
      return reply({ items: rows.results.map((row) => draftRow(row)) });
    }
    if (operation === "get-draft" || operation === "get-draft-internal") {
      return reply({ item: await fetchDraft(db, input.id, operation === "get-draft-internal") });
    }
    if (operation === "update-draft") {
      const current = await db.prepare("SELECT * FROM email_drafts WHERE id = ?")
        .bind(input.id || "").first<Record<string, unknown>>();
      if (!current) return reply({ item: null });
      if (current.status === "sent") return reply({ item: null, error: "sent" });
      const values = object(input.values);
      await db.prepare(
        "UPDATE email_drafts SET subject=?,body=?,status='draft',version=version+1,updated_at=? WHERE id=?",
      ).bind(values.subject ?? current.subject, values.body ?? current.body, input.now, input.id).run();
      return reply({ item: await fetchDraft(db, input.id) });
    }
    if (operation === "set-draft-content") {
      const result = await db.prepare(
        `UPDATE email_drafts SET subject=?,body=?,source_email_id=?,status='draft',
         version=version+1,updated_at=? WHERE id=?`,
      ).bind(input.subject || "", input.body || "", input.source_email_id || "", input.now, input.id).run();
      return reply({ item: result.meta.changes ? await fetchDraft(db, input.id) : null });
    }
    if (operation === "set-draft-status") {
      const result = await db.prepare("UPDATE email_drafts SET status=?,updated_at=? WHERE id=?")
        .bind(input.status || "draft", input.now, input.id).run();
      return reply({ item: result.meta.changes ? await fetchDraft(db, input.id) : null });
    }

    if (operation === "create-search") {
      await db.prepare(
        `INSERT INTO lead_searches (id,query,filters_json,result_limit,status,created_at,updated_at)
         VALUES (?,?,?,?,'queued',?,?)`,
      ).bind(input.id, input.query || "", JSON.stringify(input.filters || {}),
        Number(input.result_limit || 0), input.now, input.now).run();
      return reply({ item: await fetchSearch(db, input.id) });
    }
    if (operation === "finish-search") {
      const results = Array.isArray(input.results) ? input.results : [];
      const statements = [db.prepare("DELETE FROM lead_search_results WHERE search_id = ?").bind(input.id)];
      for (const value of results) {
        if (!value || typeof value !== "object") continue;
        const result = value as JsonObject;
        if (!string(result.id)) continue;
        statements.push(db.prepare(
          "INSERT INTO lead_search_results (search_id,lead_id,payload_json,imported) VALUES (?,?,?,0)",
        ).bind(input.id, result.id, JSON.stringify(result)));
      }
      statements.push(db.prepare(
        "UPDATE lead_searches SET status='succeeded',found_count=?,error='',updated_at=? WHERE id=?",
      ).bind(results.length, input.now, input.id));
      await db.batch(statements);
      return reply({ item: await fetchSearch(db, input.id) });
    }
    if (operation === "fail-search") {
      await db.prepare("UPDATE lead_searches SET status='failed',error=?,updated_at=? WHERE id=?")
        .bind(input.error || "", input.now, input.id).run();
      return reply({ updated: true });
    }
    if (operation === "list-searches") {
      const rows = await db.prepare("SELECT * FROM lead_searches ORDER BY created_at DESC")
        .all<Record<string, unknown>>();
      return reply({ items: rows.results.map(searchSummary) });
    }
    if (operation === "get-search") return reply({ item: await fetchSearch(db, input.id) });
    if (operation === "search-results" || operation === "mark-search-imported") {
      const ids = Array.isArray(input.lead_ids)
        ? input.lead_ids.filter((value): value is string => typeof value === "string" && value.length > 0) : [];
      if (!ids.length) return reply(operation === "search-results" ? { items: [] } : { updated: true });
      const marks = ids.map(() => "?").join(",");
      if (operation === "mark-search-imported") {
        await db.prepare(`UPDATE lead_search_results SET imported=1 WHERE search_id=? AND lead_id IN (${marks})`)
          .bind(input.id, ...ids).run();
        return reply({ updated: true });
      }
      const rows = await db.prepare(
        `SELECT payload_json FROM lead_search_results WHERE search_id=? AND lead_id IN (${marks})`,
      ).bind(input.id, ...ids).all<{ payload_json: string }>();
      return reply({ items: rows.results.map((item) => JSON.parse(item.payload_json)) });
    }

    if (operation === "add-mailbox-message") {
      const inserted = await db.prepare(
        `INSERT OR IGNORE INTO mailbox_messages (id,thread_id,direction,from_addr,to_addr,subject,body,sent_at)
         VALUES (?,?,?,?,?,?,?,?)`,
      ).bind(input.message_id, input.thread_id, input.direction, input.from_addr || "", input.to_addr || "",
        input.subject || "", input.body || "", input.sent_at).run();
      if (!inserted.meta.changes) {
        if (input.bounced) await db.prepare(
          "UPDATE mailbox_threads SET bounced=1,updated_at=MAX(updated_at,?) WHERE id=?",
        ).bind(input.sent_at, input.thread_id).run();
        return reply({ inserted: false });
      }
      const existing = await db.prepare("SELECT unread,replied FROM mailbox_threads WHERE id=?")
        .bind(input.thread_id).first<{ unread: number; replied: number }>();
      const inbound = await db.prepare(
        "SELECT 1 found FROM mailbox_messages WHERE thread_id=? AND direction='in' LIMIT 1",
      ).bind(input.thread_id).first();
      const unread = input.direction === "in" ? 1 : Number(existing?.unread || 0);
      const replied = input.direction === "out" && inbound ? 1 : Number(existing?.replied || 0);
      await db.prepare(
        `INSERT INTO mailbox_threads (id,subject,lead_id,last_from,unread,bounced,replied,updated_at)
         VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET subject=excluded.subject,
         last_from=excluded.last_from,unread=excluded.unread,bounced=MAX(mailbox_threads.bounced,excluded.bounced),
         replied=excluded.replied,updated_at=excluded.updated_at`,
      ).bind(input.thread_id, input.subject || "", input.lead_id || "", input.from_addr || "",
        unread, Number(Boolean(input.bounced)), replied, input.sent_at).run();
      return reply({ inserted: true });
    }
    if (operation === "list-mailbox-threads") {
      const conditions: Record<string, string> = {
        inbox: "EXISTS (SELECT 1 FROM mailbox_messages m WHERE m.thread_id=t.id AND m.direction='in')",
        sent: "EXISTS (SELECT 1 FROM mailbox_messages m WHERE m.thread_id=t.id AND m.direction='out')",
        replied: "t.replied=1", bounced: "t.bounced=1",
      };
      const where = conditions[string(input.folder)];
      if (!where) return reply({ error: "Invalid mailbox folder" }, 422);
      const page = Math.max(1, Number(input.page || 1));
      const pageSize = Math.max(1, Math.min(100, Number(input.page_size || 25)));
      const total = await db.prepare(`SELECT COUNT(*) count FROM mailbox_threads t WHERE ${where}`)
        .first<{ count: number }>();
      const rows = await db.prepare(
        `SELECT t.*,COALESCE((SELECT substr(m.body,1,240) FROM mailbox_messages m
         WHERE m.thread_id=t.id ORDER BY m.sent_at DESC LIMIT 1),'') last_snippet
         FROM mailbox_threads t WHERE ${where} ORDER BY t.updated_at DESC LIMIT ? OFFSET ?`,
      ).bind(pageSize, (page - 1) * pageSize).all<Record<string, unknown>>();
      return reply({ items: rows.results.map((row) => ({
        id: row.id, subject: row.subject, lead_id: row.lead_id, last_from: row.last_from,
        last_snippet: row.last_snippet, unread: Boolean(row.unread), updated_at: row.updated_at,
      })), total: Number(total?.count || 0) });
    }
    if (operation === "get-mailbox-thread") {
      const thread = await db.prepare("SELECT * FROM mailbox_threads WHERE id=?")
        .bind(input.id || "").first<Record<string, unknown>>();
      if (!thread) return reply({ item: null });
      const messages = await db.prepare("SELECT * FROM mailbox_messages WHERE thread_id=? ORDER BY sent_at")
        .bind(input.id).all<Record<string, unknown>>();
      return reply({ item: {
        id: thread.id, subject: thread.subject, lead_id: thread.lead_id,
        messages: messages.results.map((row) => ({ id: row.id, direction: row.direction,
          from: row.from_addr, to: row.to_addr, subject: row.subject, body: row.body, sent_at: row.sent_at })),
      } });
    }
    if (operation === "mark-thread-read") {
      const result = await db.prepare("UPDATE mailbox_threads SET unread=0 WHERE id=?").bind(input.id).run();
      return reply({ updated: Boolean(result.meta.changes) });
    }

    if (operation === "create-conversation") {
      await db.batch([
        db.prepare("INSERT INTO operator_conversations (id,created_at,updated_at) VALUES (?,?,?)")
          .bind(input.id, input.now, input.now),
        db.prepare("INSERT INTO operator_messages (id,conversation_id,role,content,created_at) VALUES (?,?,'user',?,?)")
          .bind(input.message_id, input.id, input.message || "", input.now),
      ]);
      return reply({ saved: true });
    }
    if (operation === "add-operator-message") {
      await db.batch([
        db.prepare("INSERT INTO operator_messages (id,conversation_id,role,content,tool_calls_json,created_at) VALUES (?,?,?,?,?,?)")
          .bind(input.id, input.conversation_id, input.role, input.content || "",
            input.tool_calls ? JSON.stringify(input.tool_calls) : null, input.now),
        db.prepare("UPDATE operator_conversations SET updated_at=? WHERE id=?")
          .bind(input.now, input.conversation_id),
      ]);
      return reply({ saved: true });
    }
    if (operation === "add-timeline-step") {
      const result = await db.prepare(
        "INSERT INTO operator_timeline (conversation_id,step,agent,action,status,started_at,finished_at) VALUES (?,?,?,?,?,?,?)",
      ).bind(input.conversation_id, input.step, input.agent, input.action, input.status,
        input.started_at || null, input.finished_at || null).run();
      return reply({ id: result.meta.last_row_id });
    }
    if (operation === "update-timeline-step") {
      const terminal = input.status === "succeeded" || input.status === "failed";
      await db.prepare(
        "UPDATE operator_timeline SET status=?,started_at=COALESCE(started_at,?),finished_at=? WHERE id=?",
      ).bind(input.status, input.now, terminal ? input.now : null, input.id).run();
      return reply({ updated: true });
    }
    if (operation === "list-conversations") {
      const rows = await db.prepare(
        `SELECT c.id,c.created_at,c.updated_at,COALESCE((SELECT content FROM operator_messages m
         WHERE m.conversation_id=c.id ORDER BY m.created_at DESC LIMIT 1),'') last_message
         FROM operator_conversations c ORDER BY c.updated_at DESC`,
      ).all<Record<string, unknown>>();
      return reply({ items: rows.results });
    }
    if (operation === "get-conversation" || operation === "conversation-timeline") {
      const conversation = await db.prepare("SELECT * FROM operator_conversations WHERE id=?")
        .bind(input.id || "").first<Record<string, unknown>>();
      if (!conversation) return reply(operation === "get-conversation" ? { item: null } : { found: false, items: [] });
      if (operation === "conversation-timeline") {
        const rows = await db.prepare(
          "SELECT step,agent,action,status,started_at,finished_at FROM operator_timeline WHERE conversation_id=? ORDER BY id",
        ).bind(input.id).all<Record<string, unknown>>();
        return reply({ found: true, items: rows.results });
      }
      const messages = await db.prepare(
        "SELECT role,content,tool_calls_json,created_at FROM operator_messages WHERE conversation_id=? ORDER BY created_at",
      ).bind(input.id).all<Record<string, unknown>>();
      return reply({ item: { id: conversation.id, messages: messages.results.map((row) => ({
        role: row.role, content: row.content,
        tool_calls: row.tool_calls_json ? JSON.parse(String(row.tool_calls_json)) : null,
        created_at: row.created_at,
      })) } });
    }
    return reply({ error: "Unsupported workflow operation" }, 404);
  } catch (error) {
    console.error("D1 workflow bridge failed", error);
    return reply({ error: "D1 workflow operation failed" }, 500);
  }
}

/** Non-secret application settings. Secret-shaped keys are rejected in Python
 * and never accepted by this bridge's public surface. */
export async function handleConfigStateBridge(request: Request, env: WorkflowBridgeEnv): Promise<Response> {
  if (request.method !== "POST") return reply({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/config-state\//, "");
  const input = await inputBody(request);
  if (!input) return reply({ error: "Expected JSON object" }, 400);
  try {
    if (operation === "load") {
      const configured = await env.AUTOREACH_DB.prepare(
        "SELECT value FROM app_meta WHERE key='configured_at'",
      ).first<{ value: string }>();
      const rows = await env.AUTOREACH_DB.prepare("SELECT key,value_json FROM app_settings")
        .all<{ key: string; value_json: string }>();
      return reply({
        configured: Boolean(configured?.value),
        values: Object.fromEntries(rows.results.map((item) => [item.key, JSON.parse(item.value_json)])),
      });
    }
    if (operation === "initialize") {
      const now = new Date().toISOString();
      const configured = await env.AUTOREACH_DB.prepare(
        "SELECT 1 FROM app_meta WHERE key='configured_at'",
      ).first();
      if (configured) return reply({ initialized: false });
      const values = object(input.values);
      const statements = [env.AUTOREACH_DB.prepare(
        "INSERT INTO app_meta (key,value,updated_at) VALUES ('configured_at',?,?)",
      ).bind(now, now), ...Object.entries(values).map(([key, value]) => env.AUTOREACH_DB.prepare(
        `INSERT INTO app_settings (key,value_json,updated_at) VALUES (?,?,?)
         ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`,
      ).bind(key, JSON.stringify(value), now))];
      await env.AUTOREACH_DB.batch(statements);
      return reply({ initialized: true });
    }
    if (operation === "update") {
      const now = new Date().toISOString();
      const values = object(input.values);
      const statements = Object.entries(values).map(([key, value]) => env.AUTOREACH_DB.prepare(
        `INSERT INTO app_settings (key,value_json,updated_at) VALUES (?,?,?)
         ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`,
      ).bind(key, JSON.stringify(value), now));
      if (statements.length) await env.AUTOREACH_DB.batch(statements);
      return reply({ updated: statements.length });
    }
    return reply({ error: "Unsupported config-state operation" }, 404);
  } catch (error) {
    console.error("D1 config-state bridge failed", error);
    return reply({ error: "D1 config-state operation failed" }, 500);
  }
}
