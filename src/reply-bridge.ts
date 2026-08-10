type JsonObject = Record<string, unknown>;

interface ReplyBridgeEnv { AUTOREACH_DB: D1Database }

const response = (value: unknown, status = 200) => Response.json(value, { status });

async function body(request: Request): Promise<JsonObject | null> {
  try {
    const value = await request.json<unknown>();
    return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
  } catch { return null; }
}

export async function handleReplyBridge(request: Request, env: ReplyBridgeEnv): Promise<Response> {
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  const operation = new URL(request.url).pathname.replace(/^\/v1\/replies\//, "");
  const input = await body(request);
  if (!input) return response({ error: "Expected JSON object" }, 400);
  const obj = (name: string) => input[name] && typeof input[name] === "object"
    ? input[name] as JsonObject : null;
  const now = new Date().toISOString();
  try {
    if (operation === "get-conversation") {
      const row = await env.AUTOREACH_DB.prepare("SELECT * FROM conversations WHERE lead_id=?")
        .bind(input.lead_id || "").first<Record<string, unknown>>();
      return response({ conversation: row ? { ...row, escalated: Boolean(row.escalated) } : null });
    }
    if (operation === "upsert-conversation") {
      const c = obj("conversation");
      if (!c || !c.id) return response({ error: "conversation is required" }, 422);
      await env.AUTOREACH_DB.prepare(
        `INSERT INTO conversations (id,lead_id,recipient,status,message_count,last_intent,last_sentiment,escalated,created_at,updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET recipient=excluded.recipient,
         status=excluded.status,message_count=excluded.message_count,last_intent=excluded.last_intent,
         last_sentiment=excluded.last_sentiment,escalated=excluded.escalated,updated_at=excluded.updated_at`,
      ).bind(c.id, c.lead_id || c.id, c.recipient || "", c.status || "active", Number(c.message_count || 0),
        c.last_intent || "", c.last_sentiment || "", Number(Boolean(c.escalated)),
        c.created_at || now, c.updated_at || now).run();
      return response({ saved: true });
    }
    if (operation === "count-exchanges") {
      const row = await env.AUTOREACH_DB.prepare(
        "SELECT COUNT(*) count FROM conversation_messages WHERE lead_id=? AND direction='inbound'",
      ).bind(input.lead_id || "").first<{ count: number }>();
      return response({ count: Number(row?.count || 0) });
    }
    if (operation === "add-message") {
      const m = obj("message");
      if (!m || !m.id) return response({ error: "message is required" }, 422);
      const result = await env.AUTOREACH_DB.prepare(
        `INSERT INTO conversation_messages (id,conversation_id,lead_id,direction,body,message_id,intent,sentiment,action_taken,created_at)
         VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING`,
      ).bind(m.id, m.conversation_id || "", m.lead_id || "", m.direction || "", m.body || "",
        m.message_id || "", m.intent || "", m.sentiment || "", m.action_taken || "", m.created_at || now).run();
      return response({ saved: Boolean(result.meta.changes) });
    }
    if (operation === "get-messages") {
      const rows = await env.AUTOREACH_DB.prepare(
        "SELECT * FROM conversation_messages WHERE lead_id=? ORDER BY created_at",
      ).bind(input.lead_id || "").all<Record<string, unknown>>();
      return response({ items: rows.results });
    }
    if (operation === "add-handoff") {
      const h = obj("handoff");
      if (!h || !input.id) return response({ error: "handoff is required" }, 422);
      await env.AUTOREACH_DB.prepare(
        `INSERT OR IGNORE INTO handoffs (id,lead_id,reason,urgency,summary,suggested_response,conversation_excerpt,created_at)
         VALUES (?,?,?,?,?,?,?,?)`,
      ).bind(input.id, h.lead_id || "", h.reason || "", h.urgency || "medium", h.summary || "",
        h.suggested_response || "", h.conversation_excerpt || "", h.created_at || now).run();
      return response({ saved: true });
    }
    if (operation === "add-notification") {
      const n = obj("notification");
      if (!n || !n.id) return response({ error: "notification is required" }, 422);
      await env.AUTOREACH_DB.prepare(
        `INSERT OR IGNORE INTO notifications (id,kind,lead_id,title,message,urgency,created_at)
         VALUES (?,?,?,?,?,?,?)`,
      ).bind(n.id, n.kind || "alert", n.lead_id || "", n.title || "", n.message || "",
        n.urgency || "medium", n.created_at || now).run();
      return response({ saved: true });
    }
    if (operation === "list-notifications") {
      const rows = await env.AUTOREACH_DB.prepare("SELECT * FROM notifications ORDER BY created_at DESC")
        .all<Record<string, unknown>>();
      return response({ items: rows.results });
    }
    if (operation === "upsert-job") {
      const j = obj("job");
      if (!j || !j.id) return response({ error: "job is required" }, 422);
      await env.AUTOREACH_DB.prepare(
        `INSERT INTO reply_jobs (id,status,total,handled,escalated,replies_sent,meetings_booked,skipped,created_at,completed_at)
         VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,total=excluded.total,
         handled=excluded.handled,escalated=excluded.escalated,replies_sent=excluded.replies_sent,
         meetings_booked=excluded.meetings_booked,skipped=excluded.skipped,completed_at=excluded.completed_at`,
      ).bind(j.id, j.status || "pending", Number(j.total || 0), Number(j.handled || 0),
        Number(j.escalated || 0), Number(j.replies_sent || 0), Number(j.meetings_booked || 0),
        Number(j.skipped || 0), j.created_at || now, j.completed_at || null).run();
      return response({ saved: true });
    }
    if (operation === "claim-inbound") {
      const id = String(input.id || "");
      if (!id) return response({ error: "id is required" }, 422);
      const inserted = await env.AUTOREACH_DB.prepare(
        `INSERT OR IGNORE INTO inbound_replies
         (id,provider_event_id,payload_json,status,lease_token,lease_expires_at,received_at)
         VALUES (?,?,?,'running',?,?,?)`,
      ).bind(id, input.provider_event_id || null, JSON.stringify(input.payload || {}),
        input.lease_token || "", input.lease_expires_at || now, now).run();
      if (inserted.meta.changes) return response({ claimed: true, status: "running" });
      const reclaimed = await env.AUTOREACH_DB.prepare(
        `UPDATE inbound_replies SET status='running',lease_token=?,lease_expires_at=?,error=''
         WHERE id=? AND (status='failed' OR (status='running' AND lease_expires_at<=?))`,
      ).bind(input.lease_token || "", input.lease_expires_at || now, id, now).run();
      const row = await env.AUTOREACH_DB.prepare("SELECT status FROM inbound_replies WHERE id=?")
        .bind(id).first<{ status: string }>();
      return response({ claimed: Boolean(reclaimed.meta.changes), status: row?.status || "missing" });
    }
    if (operation === "complete-inbound" || operation === "fail-inbound") {
      const success = operation === "complete-inbound";
      const result = await env.AUTOREACH_DB.prepare(
        `UPDATE inbound_replies SET status=?,processed_at=?,error=?,lease_token=NULL,lease_expires_at=NULL
         WHERE id=? AND status='running' AND lease_token=?`,
      ).bind(success ? "succeeded" : "failed", now, success ? "" : String(input.error || "").slice(0, 4000),
        input.id || "", input.lease_token || "").run();
      return response({ updated: Boolean(result.meta.changes) });
    }
    if (operation === "reserve-outbound") {
      const m = obj("message");
      if (!m || !m.id) return response({ error: "message is required" }, 422);
      const result = await env.AUTOREACH_DB.prepare(
        `INSERT OR IGNORE INTO conversation_messages
         (id,conversation_id,lead_id,direction,body,message_id,action_taken,created_at)
         VALUES (?,?,?,'outbound',?,?,'delivery_reserved',?)`,
      ).bind(m.id, m.conversation_id || "", m.lead_id || "", m.body || "",
        m.message_id || "", m.created_at || now).run();
      return response({ reserved: Boolean(result.meta.changes) });
    }
    if (operation === "finish-outbound") {
      await env.AUTOREACH_DB.prepare(
        "UPDATE conversation_messages SET message_id=?,action_taken=? WHERE id=? AND action_taken='delivery_reserved'",
      ).bind(input.message_id || "", input.success ? input.action_taken || "sent" : "delivery_ambiguous",
        input.id || "").run();
      return response({ updated: true });
    }
    if (operation === "stop-sequence") {
      await env.AUTOREACH_DB.prepare(
        "UPDATE sequence_states SET status=?,next_send_at_utc=NULL,updated_at=? WHERE lead_id=? AND status='active'",
      ).bind(input.status || "replied", now, input.lead_id || "").run();
      return response({ updated: true });
    }
    return response({ error: "Unsupported replies operation" }, 404);
  } catch (error) {
    console.error("D1 replies bridge failed", error);
    return response({ error: "D1 replies operation failed" }, 500);
  }
}
