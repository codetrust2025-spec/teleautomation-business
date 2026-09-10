-- Bounded, versioned recovery for calendar invitations that a previous
-- detection rule ignored.  It preserves the mail and every original audit row;
-- one source message is reconsidered at most once per recovery version unless
-- it is explicitly retryable.

CREATE TABLE IF NOT EXISTS recruitment_calendar_recovery (
  mailbox_message_id text PRIMARY KEY REFERENCES mailbox_messages(id) ON DELETE RESTRICT,
  detection_version text NOT NULL,
  state text NOT NULL,
  attempts integer NOT NULL DEFAULT 0,
  last_reason text,
  next_attempt_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (state IN ('RUNNING','AUTO_IGNORE','AI_RETRY_PENDING','AUTO_BOOKED','AUTO_RESCHEDULED','AUTO_CANCELLED'))
);

CREATE INDEX IF NOT EXISTS idx_recruitment_calendar_recovery_due
  ON recruitment_calendar_recovery(detection_version, state, next_attempt_at);
