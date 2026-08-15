-- Provenance and evidence strength on every finding.
--
-- A job portal relaying "your profile has been shortlisted for our top client"
-- is running a campaign; a company writing the same words has made a decision.
-- Storing who spoke, and how much weight that carries, is what stops the
-- second from being inferred from the first.

ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS source_type text;
ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS evidence_strength text;
-- Which application this finding belongs to. Outcomes from different
-- companies are different lifecycles and are never merged.
ALTER TABLE mail_outcome_audit_findings
  ADD COLUMN IF NOT EXISTS application_key text;

ALTER TABLE mail_outcome_audit_findings
  DROP CONSTRAINT IF EXISTS mail_outcome_audit_findings_strength_check;
ALTER TABLE mail_outcome_audit_findings
  ADD CONSTRAINT mail_outcome_audit_findings_strength_check
  CHECK (
    evidence_strength IS NULL
    OR evidence_strength IN ('STRONG','MODERATE','WEAK')
  );

CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_application
  ON mail_outcome_audit_findings(canonical_candidate_id, application_key);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_strength
  ON mail_outcome_audit_findings(evidence_strength, outcome);
