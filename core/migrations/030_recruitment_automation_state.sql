-- Durable, non-human projection for mail automation.  This is additive: it
-- does not erase the original classifier output, booking audit, or lifecycle
-- history.  Runtime promotion moves legacy review rows safely and audibly.

ALTER TABLE ai_recruitment_events
  ADD COLUMN IF NOT EXISTS automation_state text;

CREATE INDEX IF NOT EXISTS idx_ai_recruitment_events_automation_state
  ON ai_recruitment_events(automation_state, updated_at DESC);

CREATE TABLE IF NOT EXISTS recruitment_automation_transitions (
  id text PRIMARY KEY,
  mailbox_message_id text NOT NULL REFERENCES mailbox_messages(id) ON DELETE RESTRICT,
  ai_recruitment_event_id text REFERENCES ai_recruitment_events(id) ON DELETE RESTRICT,
  automation_state text NOT NULL,
  reason text,
  booking_id text,
  dedupe_key text NOT NULL UNIQUE,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (automation_state IN (
    'AUTO_BOOK','AUTO_IGNORE','AI_RETRY_PENDING',
    'AUTO_BOOKED','AUTO_CANCELLED','AUTO_RESCHEDULED'
  ))
);

CREATE INDEX IF NOT EXISTS idx_recruitment_automation_transitions_message
  ON recruitment_automation_transitions(mailbox_message_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recruitment_automation_transitions_event
  ON recruitment_automation_transitions(ai_recruitment_event_id, created_at DESC)
  WHERE ai_recruitment_event_id IS NOT NULL;
