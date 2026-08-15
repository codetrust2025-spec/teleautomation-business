-- Canonical relationship for a recovered interview email that corresponds to
-- a booking which was already confirmed through another supported path.
--
-- This is deliberately kept on interview_mail_analyses rather than
-- interview_auto_booking_audit: reconciliation neither creates a booking nor
-- represents an automatic-booking success/failure.

ALTER TABLE interview_mail_analyses
  ADD COLUMN IF NOT EXISTS reconciled_booking_id text;

ALTER TABLE interview_mail_analyses
  ADD COLUMN IF NOT EXISTS reconciliation_status text;

ALTER TABLE interview_mail_analyses
  ADD COLUMN IF NOT EXISTS reconciled_by text;

ALTER TABLE interview_mail_analyses
  ADD COLUMN IF NOT EXISTS reconciled_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'interview_mail_reconciliation_status_check'
      AND conrelid = 'interview_mail_analyses'::regclass
  ) THEN
    ALTER TABLE interview_mail_analyses
      ADD CONSTRAINT interview_mail_reconciliation_status_check
      CHECK (
        (
          reconciled_booking_id IS NULL
          AND reconciliation_status IS NULL
          AND reconciled_by IS NULL
          AND reconciled_at IS NULL
        )
        OR (
          reconciled_booking_id IS NOT NULL
          AND reconciliation_status = 'EXISTING_BOOKING_LINKED'
          AND NULLIF(BTRIM(reconciled_by), '') IS NOT NULL
          AND reconciled_at IS NOT NULL
        )
      );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_interview_analysis_reconciled_booking
  ON interview_mail_analyses(reconciled_booking_id)
  WHERE reconciled_booking_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_recruitment_audit_reconciled_source
  ON recruitment_audit_log(source_id)
  WHERE action = 'INTERVIEW_MESSAGE_RECONCILED';
