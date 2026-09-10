-- One authoritative, ordered state per logical interview.  This is additive:
-- existing candidate rows, booking audits and notifications remain historical
-- evidence and are never rewritten by the migration.

CREATE TABLE IF NOT EXISTS interview_lifecycle_states (
  interview_key text PRIMARY KEY,
  candidate_id text NOT NULL,
  calendar_uid text,
  calendar_sequence integer NOT NULL DEFAULT 0,
  source_message_id text NOT NULL,
  source_sent_at timestamptz,
  classification text NOT NULL,
  lifecycle_state text NOT NULL,
  schedule jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text NOT NULL UNIQUE,
  booking_id text,
  transition_status text NOT NULL DEFAULT 'PENDING',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (lifecycle_state IN (
    'DETECTED','VALIDATING','READY_TO_BOOK','BOOKED','RESCHEDULED',
    'CANCELLED','BLOCKED','NEEDS_REVIEW','FAILED'
  )),
  CHECK (transition_status IN ('PENDING','APPLIED','BLOCKED','FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_interview_lifecycle_candidate
  ON interview_lifecycle_states(candidate_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_interview_lifecycle_calendar
  ON interview_lifecycle_states(candidate_id, calendar_uid, calendar_sequence DESC)
  WHERE calendar_uid IS NOT NULL;

-- A state row is deliberately a projection. This append-only transition
-- ledger retains every idempotency key after a newer update becomes current.
CREATE TABLE IF NOT EXISTS interview_lifecycle_transitions (
  id text PRIMARY KEY,
  interview_key text NOT NULL REFERENCES interview_lifecycle_states(interview_key) ON DELETE RESTRICT,
  idempotency_key text NOT NULL UNIQUE,
  source_message_id text NOT NULL,
  classification text NOT NULL,
  lifecycle_state text NOT NULL,
  schedule jsonb NOT NULL DEFAULT '{}'::jsonb,
  booking_id text,
  transition_status text NOT NULL DEFAULT 'PENDING',
  failure_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  CHECK (transition_status IN ('PENDING','APPLIED','BLOCKED','FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_interview_lifecycle_transition_key
  ON interview_lifecycle_transitions(interview_key, created_at DESC);
