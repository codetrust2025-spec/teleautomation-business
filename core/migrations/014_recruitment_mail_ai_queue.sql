-- Decouple durable Gmail ingestion from bounded semantic AI processing.
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS authentication_results text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS received_spf text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS rfc_message_id text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS ai_lease_expires_at timestamptz;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS ai_last_error_code text;

CREATE INDEX IF NOT EXISTS mailbox_messages_ai_ready_idx
  ON mailbox_messages(ai_retry_after, sent_at)
  WHERE processing_status IN ('AI_QUEUED', 'AI_RETRY_PENDING', 'AI_RUNNING');

-- FILTERED is the pre-analysis transient state.  Rows left there across a
-- deployment/crash were inserted durably but never completed semantic work.
UPDATE mailbox_messages SET processing_status='AI_QUEUED', updated_at=now()
WHERE processing_status='FILTERED';
