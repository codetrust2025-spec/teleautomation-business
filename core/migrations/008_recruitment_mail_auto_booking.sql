-- Additive Gmail Pub/Sub and AI interview auto-booking persistence.
-- Candidate/payment/slot data remains owned by the existing candidate store.

ALTER TABLE candidate_mailboxes ADD COLUMN IF NOT EXISTS gmail_watch_expiration timestamptz;
ALTER TABLE candidate_mailboxes ADD COLUMN IF NOT EXISTS gmail_watch_topic text;
ALTER TABLE candidate_mailboxes ADD COLUMN IF NOT EXISTS last_push_history_id text;

CREATE TABLE IF NOT EXISTS gmail_pubsub_deliveries (
  pubsub_message_id text PRIMARY KEY,
  subscription text,
  email_address text,
  history_id text,
  mailbox_id text REFERENCES candidate_mailboxes(id) ON DELETE SET NULL,
  delivery_status text NOT NULL,
  error_code text,
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz
);

CREATE TABLE IF NOT EXISTS interview_mail_analyses (
  id text PRIMARY KEY,
  mailbox_message_id text NOT NULL REFERENCES mailbox_messages(id) ON DELETE CASCADE,
  email_analysis_id text REFERENCES mail_ai_analyses(id) ON DELETE SET NULL,
  gmail_account_id text,
  gmail_message_id text NOT NULL,
  gmail_thread_id text,
  candidate_id text NOT NULL,
  classification text NOT NULL,
  is_interview_email boolean NOT NULL DEFAULT false,
  company_name text,
  job_role text,
  interview_round text,
  interview_date date,
  interview_time text,
  timezone text,
  meeting_link text,
  interview_mode text,
  location text,
  ai_confidence double precision NOT NULL DEFAULT 0,
  ai_summary text,
  ai_reason text,
  validation_status text NOT NULL,
  processing_status text NOT NULL,
  structured_result jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(mailbox_message_id)
);

CREATE TABLE IF NOT EXISTS interview_auto_booking_audit (
  id text PRIMARY KEY,
  booking_id text,
  source text NOT NULL DEFAULT 'AI Mail Monitoring',
  gmail_message_id text NOT NULL,
  gmail_thread_id text,
  email_analysis_id text REFERENCES interview_mail_analyses(id) ON DELETE SET NULL,
  candidate_id text NOT NULL,
  classification text NOT NULL,
  auto_booked boolean NOT NULL DEFAULT false,
  validation_status text NOT NULL,
  payment_validation_status text,
  duplicate_check_status text,
  conflict_check_status text,
  booking_status text NOT NULL,
  previous_booking jsonb,
  new_booking jsonb,
  failure_code text,
  failure_message text,
  correlation_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(gmail_message_id, classification)
);

ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS booking_id text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS booking_audit_id text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS booking_status text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS interview_round text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS interview_date date;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS interview_time text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS interview_timezone text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS interview_mode text;
ALTER TABLE mail_monitoring_notifications ADD COLUMN IF NOT EXISTS meeting_link text;

CREATE INDEX IF NOT EXISTS idx_pubsub_delivery_mailbox ON gmail_pubsub_deliveries(mailbox_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_interview_analysis_candidate ON interview_mail_analyses(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_interview_booking_candidate ON interview_auto_booking_audit(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_interview_booking_id ON interview_auto_booking_audit(booking_id);
