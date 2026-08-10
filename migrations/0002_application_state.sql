-- Canonical relational state for the D1 cutover. JSON payloads stay in D1 only
-- when queryable/small; large source material is referenced by agent_artifacts.

CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY, state TEXT NOT NULL, email TEXT, company TEXT, industry TEXT,
  quality_score REAL, company_size_score REAL, industry_fit_score REAL, recency_score REAL,
  manual_bump INTEGER NOT NULL DEFAULT 0, priority REAL, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, retry_after TEXT, discovered_at TEXT, sent_at TEXT, replied_at TEXT,
  state_entered_at TEXT, source_job TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state);

CREATE TABLE IF NOT EXISTS orchestrator_runs (
  id TEXT PRIMARY KEY, stage TEXT NOT NULL, agent TEXT NOT NULL, processed INTEGER NOT NULL,
  succeeded INTEGER NOT NULL, failed INTEGER NOT NULL, ok INTEGER NOT NULL, error TEXT,
  started_at TEXT NOT NULL, duration_seconds REAL
);
CREATE TABLE IF NOT EXISTS lead_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id TEXT NOT NULL, from_state TEXT,
  to_state TEXT, note TEXT, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lead_events_lead ON lead_events(lead_id, at);
CREATE TABLE IF NOT EXISTS dead_letters (
  lead_id TEXT PRIMARY KEY, stage TEXT NOT NULL, reason TEXT NOT NULL, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, user_prompt TEXT NOT NULL, brief_json TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

CREATE TABLE IF NOT EXISTS research_documents (
  id TEXT PRIMARY KEY, lead_id TEXT, company TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '', sections_json TEXT NOT NULL DEFAULT '[]',
  sources_json TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_documents_lead ON research_documents(lead_id, updated_at);
CREATE TABLE IF NOT EXISTS email_drafts (
  id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
  version INTEGER NOT NULL DEFAULT 1, tone TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '',
  source_email_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_drafts_lookup ON email_drafts(lead_id, campaign_id, status, updated_at);
CREATE TABLE IF NOT EXISTS lead_searches (
  id TEXT PRIMARY KEY, query TEXT NOT NULL, filters_json TEXT NOT NULL DEFAULT '{}',
  result_limit INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued', found_count INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lead_search_results (
  search_id TEXT NOT NULL, lead_id TEXT NOT NULL, payload_json TEXT NOT NULL,
  imported INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(search_id, lead_id)
);

CREATE TABLE IF NOT EXISTS sent_emails (
  id TEXT PRIMARY KEY, email_id TEXT NOT NULL, lead_id TEXT NOT NULL, step TEXT, recipient TEXT,
  account_email TEXT, provider TEXT, message_id TEXT, idempotency_key TEXT UNIQUE, subject TEXT,
  body TEXT, status TEXT NOT NULL DEFAULT 'queued', opened INTEGER NOT NULL DEFAULT 0,
  clicked INTEGER NOT NULL DEFAULT 0, replied INTEGER NOT NULL DEFAULT 0, bounced INTEGER NOT NULL DEFAULT 0,
  sent_at TEXT, job_id TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_emails_lead ON sent_emails(lead_id);
CREATE TABLE IF NOT EXISTS tracking_events (
  id TEXT PRIMARY KEY, sent_email_id TEXT NOT NULL, lead_id TEXT, event_type TEXT NOT NULL,
  detail TEXT, bounce_type TEXT, provider_event_id TEXT UNIQUE, occurred_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sequence_states (
  lead_id TEXT PRIMARY KEY, email_id TEXT, current_step TEXT, status TEXT NOT NULL,
  steps_sent_json TEXT NOT NULL DEFAULT '[]', next_send_at_utc TEXT, workflow_instance_id TEXT,
  initial_sent_at TEXT, recipient TEXT, account_email TEXT, timezone TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sending_accounts (
  email TEXT PRIMARY KEY, provider TEXT, display_name TEXT, daily_limit INTEGER, hourly_limit INTEGER,
  sent_today INTEGER NOT NULL DEFAULT 0, sent_this_hour INTEGER NOT NULL DEFAULT 0,
  health_score REAL NOT NULL DEFAULT 1.0, status TEXT NOT NULL DEFAULT 'active', warmup_start_date TEXT
);
CREATE TABLE IF NOT EXISTS suppression_list (
  value TEXT PRIMARY KEY, is_domain INTEGER NOT NULL DEFAULT 0, reason TEXT, detail TEXT, added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, lead_id TEXT, recipient TEXT, status TEXT NOT NULL DEFAULT 'active',
  message_count INTEGER NOT NULL DEFAULT 0, last_intent TEXT, last_sentiment TEXT,
  escalated INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, lead_id TEXT, direction TEXT NOT NULL,
  body TEXT NOT NULL, message_id TEXT, intent TEXT, sentiment TEXT, action_taken TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation ON conversation_messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS handoffs (
  id TEXT PRIMARY KEY, lead_id TEXT, reason TEXT, urgency TEXT, summary TEXT,
  suggested_response TEXT, conversation_excerpt TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, lead_id TEXT, title TEXT NOT NULL,
  message TEXT NOT NULL, urgency TEXT, created_at TEXT NOT NULL
);
