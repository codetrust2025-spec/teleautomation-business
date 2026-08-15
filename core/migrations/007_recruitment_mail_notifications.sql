-- Durable mail-monitoring notifications, canonical classifications, candidate
-- job-status state, AI analysis records, review evaluation data, and replayable
-- WebSocket events.  This migration is additive and leaves the legacy
-- selection/offer review schema and data intact.

ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS classification text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS candidate_status text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS ai_reason text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS recommended_action text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS original_ai_result jsonb;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS corrected_result jsonb;
ALTER TABLE candidate_status_history ADD COLUMN IF NOT EXISTS gmail_message_id text;
ALTER TABLE candidate_status_history ADD COLUMN IF NOT EXISTS ai_classification text;
ALTER TABLE candidate_status_history ADD COLUMN IF NOT EXISTS updated_by text;

CREATE TABLE IF NOT EXISTS candidate_job_status (
  candidate_id text PRIMARY KEY,
  status text NOT NULL,
  status_rank integer NOT NULL DEFAULT 0,
  source text NOT NULL,
  source_id text,
  gmail_message_id text,
  classification text,
  confidence double precision,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_ai_analyses (
  id text PRIMARY KEY,
  mailbox_message_id text NOT NULL REFERENCES mailbox_messages(id) ON DELETE CASCADE,
  candidate_id text NOT NULL,
  model_name text,
  model_version text,
  classification text NOT NULL,
  candidate_status text,
  confidence double precision NOT NULL DEFAULT 0,
  summary text,
  reason text,
  recommended_action text,
  raw_ai_response jsonb,
  validated_response jsonb,
  processing_status text NOT NULL,
  error_code text,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(mailbox_message_id)
);

CREATE TABLE IF NOT EXISTS mail_monitoring_notifications (
  id text PRIMARY KEY,
  candidate_id text NOT NULL,
  candidate_name text,
  candidate_email text,
  gmail_account_id text,
  gmail_message_id text NOT NULL,
  gmail_thread_id text,
  email_analysis_id text REFERENCES mail_ai_analyses(id),
  ai_recruitment_event_id text REFERENCES ai_recruitment_events(id),
  notification_type text NOT NULL DEFAULT 'job_status_update',
  classification text NOT NULL,
  candidate_status text,
  company_name text,
  job_role text,
  email_subject text,
  sender_name text,
  sender_email text,
  email_received_at timestamptz,
  ai_confidence double precision NOT NULL DEFAULT 0,
  ai_summary text,
  ai_reason text,
  recommended_action text,
  priority text NOT NULL DEFAULT 'informational',
  is_read boolean NOT NULL DEFAULT false,
  read_at timestamptz,
  is_reviewed boolean NOT NULL DEFAULT false,
  reviewed_at timestamptz,
  reviewed_by text,
  review_notes text,
  is_false_detection boolean NOT NULL DEFAULT false,
  dismissed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(gmail_message_id, classification)
);

CREATE TABLE IF NOT EXISTS mail_review_evaluations (
  id text PRIMARY KEY,
  notification_id text NOT NULL REFERENCES mail_monitoring_notifications(id),
  email_analysis_id text REFERENCES mail_ai_analyses(id),
  original_classification text,
  corrected_classification text,
  original_candidate_status text,
  corrected_candidate_status text,
  original_confidence double precision,
  is_false_detection boolean NOT NULL DEFAULT false,
  review_notes text,
  reviewed_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_realtime_events (
  id text PRIMARY KEY,
  event_type text NOT NULL,
  notification_id text,
  candidate_id text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mail_notifications_candidate ON mail_monitoring_notifications(candidate_id);
CREATE INDEX IF NOT EXISTS idx_mail_notifications_message ON mail_monitoring_notifications(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_mail_notifications_classification ON mail_monitoring_notifications(classification);
CREATE INDEX IF NOT EXISTS idx_mail_notifications_read ON mail_monitoring_notifications(is_read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_notifications_reviewed ON mail_monitoring_notifications(is_reviewed, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_notifications_created ON mail_monitoring_notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_analysis_status ON mail_ai_analyses(processing_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_realtime_created ON mail_realtime_events(created_at DESC);
