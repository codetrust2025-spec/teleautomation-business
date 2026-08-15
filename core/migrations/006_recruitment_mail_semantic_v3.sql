-- Semantic-first outcome classifier metadata and safe historical retry support.
-- No historical event is promoted by SQL: only the Ollama pipeline may make
-- that business decision during a historical rescan.

ALTER TABLE mailbox_messages
  ADD COLUMN IF NOT EXISTS semantic_classifier_version text;

CREATE INDEX IF NOT EXISTS idx_mailbox_messages_semantic_retry
  ON mailbox_messages(mailbox_id, processing_status, sent_at DESC)
  WHERE processing_status IN (
    'AI_PROCESSING_FAILED', 'IGNORED_NOT_OFFER_RELATED',
    'IGNORED_LOW_CONFIDENCE'
  );

UPDATE mailbox_messages
SET semantic_classifier_version = COALESCE(semantic_classifier_version, 'legacy')
WHERE semantic_classifier_version IS NULL;
