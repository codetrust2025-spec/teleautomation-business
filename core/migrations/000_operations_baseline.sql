-- Fresh Operations database baseline. Runtime values/data are never embedded.
CREATE TABLE IF NOT EXISTS operations_schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS candidates_store (
    id TEXT PRIMARY KEY,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_store_logged_date
    ON candidates_store ((payload->>'logged_date'));
