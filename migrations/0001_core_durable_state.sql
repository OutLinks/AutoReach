-- Cloudflare D1 foundation for the production durability cutover.
-- Python still uses the local SQLite adapter until the D1 repository adapter is
-- introduced. Do not mark a deployment production-ready before that cutover.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
    ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease_expires_at
    ON jobs(lease_expires_at);

CREATE TABLE IF NOT EXISTS job_attempts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    attempt_number INTEGER NOT NULL,
    executor_id TEXT NOT NULL,
    lease_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(job_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS job_outbox (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    dispatched_at TEXT,
    queue_message_id TEXT,
    retry_after TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_outbox_retry_after
    ON job_outbox(dispatched_at, retry_after);

CREATE TABLE IF NOT EXISTS agent_artifacts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    lead_id TEXT,
    campaign_id TEXT,
    source_job_id TEXT,
    r2_key TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    content_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_artifacts_lead_kind
    ON agent_artifacts(lead_id, kind, created_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lead_pipeline_jobs (
    id TEXT PRIMARY KEY,
    job_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lead_pipeline_jobs_expires_at
    ON lead_pipeline_jobs(expires_at);

CREATE TABLE IF NOT EXISTS lead_stage_records (
    job_id TEXT NOT NULL REFERENCES lead_pipeline_jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    lead_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (job_id, stage, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_lead_stage_records_expiry
    ON lead_stage_records(expires_at);

CREATE TABLE IF NOT EXISTS lead_dedup_keys (
    key_type TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (key_type, value_hash)
);
