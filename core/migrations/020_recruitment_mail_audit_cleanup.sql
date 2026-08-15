-- Selection-audit cleanup.
--
-- Findings that do not belong in the selection totals are marked, never
-- deleted. The mail, its evidence, its attachments and the finding's own
-- history stay exactly as they were; only the counting changes. Every
-- suppression records who decided, why, and when, so the exclusion is as
-- auditable as the finding it removes.

ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS suppressed boolean NOT NULL DEFAULT false;
ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS suppression_reason text;
ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS suppression_detail text;
-- Which audit the suppression applies to. Cleanup is evaluated for the
-- selection audit; an interview-slot result suppressed here is still a
-- first-class result in the Interview Slot Audit.
ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS suppression_mode text;
ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS suppressed_at timestamptz;

ALTER TABLE mail_outcome_audit_findings
  DROP CONSTRAINT IF EXISTS mail_outcome_audit_findings_suppression_check;
ALTER TABLE mail_outcome_audit_findings
  ADD CONSTRAINT mail_outcome_audit_findings_suppression_check
  CHECK (
    suppression_reason IS NULL
    OR suppression_reason IN ('IRRELEVANT','DUPLICATE','SUPERSEDED','WRONG_AUDIT_MODE')
  );

-- Append-only trail of cleanup decisions. A finding that is later un-suppressed
-- (because a re-audit changed its outcome) keeps the record of why it was
-- excluded in the first place.
CREATE TABLE IF NOT EXISTS mail_outcome_audit_cleanup_log (
  id text PRIMARY KEY,
  finding_id text NOT NULL REFERENCES mail_outcome_audit_findings(id) ON DELETE CASCADE,
  canonical_candidate_id text NOT NULL,
  run_id text,
  mode text NOT NULL DEFAULT 'SELECTION',
  action text NOT NULL,
  reason text,
  detail text,
  previous_reason text,
  decided_by text NOT NULL DEFAULT 'system',
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT mail_outcome_audit_cleanup_action_check
    CHECK (action IN ('SUPPRESSED','RESTORED','REASON_CHANGED'))
);

CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_suppressed
  ON mail_outcome_audit_findings(canonical_candidate_id, suppressed);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_cleanup_finding
  ON mail_outcome_audit_cleanup_log(finding_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_cleanup_candidate
  ON mail_outcome_audit_cleanup_log(canonical_candidate_id, created_at DESC);
