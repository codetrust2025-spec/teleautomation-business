-- Durable Gmail discovery queue.  Gmail history cursors may only advance after
-- every message ID in the history response has been committed here.
CREATE TABLE IF NOT EXISTS gmail_message_ingestion_queue (
    id text PRIMARY KEY,
    mailbox_id text NOT NULL REFERENCES candidate_mailboxes(id) ON DELETE CASCADE,
    provider_message_id text NOT NULL,
    source_history_id text,
    discovery_source text NOT NULL DEFAULT 'GMAIL_HISTORY',
    status text NOT NULL DEFAULT 'QUEUED',
    attempts integer NOT NULL DEFAULT 0,
    last_error_code text,
    last_error_message text,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT gmail_ingestion_mailbox_message_unique
        UNIQUE (mailbox_id, provider_message_id),
    CONSTRAINT gmail_ingestion_status_check
        CHECK (status IN ('QUEUED','RUNNING','COMPLETED','DELETED','DEAD_LETTER'))
);

CREATE INDEX IF NOT EXISTS gmail_ingestion_ready_idx
    ON gmail_message_ingestion_queue(mailbox_id, discovered_at)
    WHERE status = 'QUEUED';

CREATE INDEX IF NOT EXISTS gmail_ingestion_status_idx
    ON gmail_message_ingestion_queue(status, updated_at);
