-- Production cutover additions for durable orchestration and API workflows.

ALTER TABLE leads ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_threads (
  id TEXT PRIMARY KEY,
  subject TEXT NOT NULL DEFAULT '',
  lead_id TEXT NOT NULL,
  last_from TEXT NOT NULL DEFAULT '',
  unread INTEGER NOT NULL DEFAULT 0,
  bounced INTEGER NOT NULL DEFAULT 0,
  replied INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mailbox_threads_updated ON mailbox_threads(updated_at DESC);

CREATE TABLE IF NOT EXISTS mailbox_messages (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  from_addr TEXT NOT NULL DEFAULT '',
  to_addr TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mailbox_messages_thread ON mailbox_messages(thread_id, sent_at);

CREATE TABLE IF NOT EXISTS operator_conversations (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  tool_calls_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_messages_conversation
  ON operator_messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS operator_timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  step TEXT NOT NULL,
  agent TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_operator_timeline_conversation
  ON operator_timeline(conversation_id, id);

CREATE TABLE IF NOT EXISTS reply_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  total INTEGER NOT NULL DEFAULT 0,
  handled INTEGER NOT NULL DEFAULT 0,
  escalated INTEGER NOT NULL DEFAULT 0,
  replies_sent INTEGER NOT NULL DEFAULT 0,
  meetings_booked INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS inbound_replies (
  id TEXT PRIMARY KEY,
  provider_event_id TEXT UNIQUE,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  lease_token TEXT,
  lease_expires_at TEXT,
  received_at TEXT NOT NULL,
  processed_at TEXT,
  error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inbound_replies_pending
  ON inbound_replies(status, received_at);

CREATE TABLE IF NOT EXISTS send_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  total INTEGER NOT NULL DEFAULT 0,
  sent INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  suppressed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

-- Capacity is reserved before provider I/O. The deterministic idempotency key
-- makes a retry reuse the same reservation while the time indexes keep volume
-- governance correct across container restarts.
CREATE TABLE IF NOT EXISTS send_reservations (
  idempotency_key TEXT PRIMARY KEY,
  account_email TEXT NOT NULL,
  recipient_domain TEXT NOT NULL,
  reserved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_send_reservations_account_time
  ON send_reservations(account_email, reserved_at);
CREATE INDEX IF NOT EXISTS idx_send_reservations_domain_time
  ON send_reservations(recipient_domain, reserved_at);
