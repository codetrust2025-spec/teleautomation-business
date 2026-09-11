-- Add the automated lifecycle vocabulary without rewriting audit history.
-- The legacy value remains readable only for historical rows/rollback.
ALTER TABLE interview_lifecycle_states
  DROP CONSTRAINT IF EXISTS interview_lifecycle_states_lifecycle_state_check;
ALTER TABLE interview_lifecycle_states
  ADD CONSTRAINT interview_lifecycle_states_lifecycle_state_check CHECK (
    lifecycle_state IN ('DETECTED','VALIDATING','READY_TO_BOOK','BOOKED',
      'RESCHEDULED','CANCELLED','BLOCKED','AI_RETRY_PENDING','FAILED','NEEDS_REVIEW')
  );
