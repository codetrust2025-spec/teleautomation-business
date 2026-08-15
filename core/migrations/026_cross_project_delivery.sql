-- Durable, idempotent service-to-service command/event state.
CREATE TABLE IF NOT EXISTS cross_project_inbox (
    idempotency_key TEXT PRIMARY KEY,
    contract_name TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'PROCESSING',
    response JSONB,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS cross_project_outbox (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    contract_name TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_cross_project_outbox_pending
    ON cross_project_outbox (next_attempt_at, created_at)
    WHERE status IN ('PENDING', 'RETRY');
