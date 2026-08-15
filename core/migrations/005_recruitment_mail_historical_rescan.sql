-- Historical reprocessing metadata for recruitment_email_status_extraction_v2.
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS body_text text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS html_body_text text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS previous_processing_status text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS reprocessed_at timestamptz;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS reprocessing_reason text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS reprocessing_prompt_version text;

ALTER TABLE mailbox_sync_jobs ADD COLUMN IF NOT EXISTS job_type text NOT NULL DEFAULT 'INCREMENTAL';
ALTER TABLE mailbox_sync_jobs ADD COLUMN IF NOT EXISTS range_start date;
ALTER TABLE mailbox_sync_jobs ADD COLUMN IF NOT EXISTS range_end date;

CREATE INDEX IF NOT EXISTS idx_mailbox_messages_rescan_signals
  ON mailbox_messages(mailbox_id, sent_at DESC);
