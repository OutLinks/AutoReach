-- Agent 2 profile index. Profile JSON lives in R2; D1 supplies the durable,
-- queryable selection fields that Agent 3 needs before fetching an artifact.

CREATE TABLE IF NOT EXISTS research_profile_index (
  id TEXT PRIMARY KEY,
  lead_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  artifact_key TEXT NOT NULL,
  status TEXT NOT NULL,
  quality_score REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_profile_index_selection
  ON research_profile_index(status, quality_score, updated_at);
CREATE INDEX IF NOT EXISTS idx_research_profile_index_lead
  ON research_profile_index(lead_id, updated_at DESC);
