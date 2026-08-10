-- Agent 3's durable delivery-ready output. Recipient/timezone fields are a
-- snapshot taken at composition time so Agent 4 never joins local JSONL files.

CREATE TABLE IF NOT EXISTS written_emails (
  id TEXT PRIMARY KEY,
  lead_id TEXT NOT NULL,
  research_profile_id TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  hook TEXT NOT NULL DEFAULT '',
  cta TEXT NOT NULL DEFAULT '',
  lead_first_name TEXT NOT NULL DEFAULT '',
  lead_last_name TEXT NOT NULL DEFAULT '',
  lead_company TEXT NOT NULL DEFAULT '',
  recipient TEXT NOT NULL DEFAULT '',
  timezone TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  sender_name TEXT NOT NULL DEFAULT '',
  sender_email TEXT NOT NULL DEFAULT '',
  tone TEXT NOT NULL DEFAULT '',
  template_name TEXT NOT NULL DEFAULT '',
  quality_score REAL NOT NULL DEFAULT 0,
  quality_passed INTEGER NOT NULL DEFAULT 0,
  quality_report_json TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  job_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_written_emails_sendable
  ON written_emails(status, created_at);
CREATE INDEX IF NOT EXISTS idx_written_emails_lead
  ON written_emails(lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_written_emails_job
  ON written_emails(job_id, created_at);

CREATE TABLE IF NOT EXISTS email_writer_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  total INTEGER NOT NULL DEFAULT 0,
  written INTEGER NOT NULL DEFAULT 0,
  quality_passed INTEGER NOT NULL DEFAULT 0,
  quality_failed INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT
);
