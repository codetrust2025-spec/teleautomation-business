-- Ollama-assisted mail audit: its own queue, its own cache, its own results.
--
-- Deliberately shares no table with the interview auto-booking feature. The
-- booking pipeline's queue (mailbox_sync_jobs), its analyses
-- (interview_mail_analyses) and its audit trail (interview_auto_booking_audit)
-- are neither written nor referenced here. Dropping every object in this file
-- would leave auto-booking completely intact.

CREATE TABLE IF NOT EXISTS mail_audit_ai_queue (
  id text PRIMARY KEY,
  finding_id text NOT NULL REFERENCES mail_outcome_audit_findings(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'PENDING',
  attempts integer NOT NULL DEFAULT 0,
  requested_by text,
  last_error text,
  retry_after timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- One pending review per finding: the audit never multiplies its own work.
  CONSTRAINT mail_audit_ai_queue_finding_unique UNIQUE (finding_id),
  CONSTRAINT mail_audit_ai_queue_status_check
    CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','DEFERRED'))
);

-- Advisory second opinions. Nothing here overwrites a finding: the rule
-- engine's outcome stands until a human decides otherwise.
CREATE TABLE IF NOT EXISTS mail_audit_ai_results (
  id text PRIMARY KEY,
  cache_key text NOT NULL UNIQUE,
  finding_id text NOT NULL REFERENCES mail_outcome_audit_findings(id) ON DELETE CASCADE,
  prompt_name text NOT NULL,
  model text,
  agrees boolean NOT NULL DEFAULT true,
  suggested_outcome text,
  confidence double precision NOT NULL DEFAULT 0,
  reasoning text,
  is_bulk_campaign boolean NOT NULL DEFAULT false,
  sender_is_hiring_company boolean NOT NULL DEFAULT false,
  quoted_evidence text,
  raw_response jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Separate log. Booking keeps recruitment_audit_log; this never writes there.
CREATE TABLE IF NOT EXISTS mail_audit_ai_log (
  id text PRIMARY KEY,
  event text NOT NULL,
  finding_id text,
  detail text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Citations, and whether they survived checking against the source material.
-- An unverified review is kept and shown, but marked, so a fabricated quote is
-- visible rather than believed.
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS cited_message_id text;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS cited_attachment text;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS cited_company text;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS verified boolean NOT NULL DEFAULT false;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS verification_problems text;

-- Hardening after the first production batch. The model's raw answer is kept
-- verbatim in raw_response; these columns hold what the system derived from it
-- deterministically, which is what the UI shows and what any approval rests on.
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS normalized_confidence double precision;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS derived_agreement text;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS restricted_outcome text;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS restrictions text;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS sender_verified_company boolean NOT NULL DEFAULT false;
ALTER TABLE mail_audit_ai_results
  ADD COLUMN IF NOT EXISTS approval_state text;

CREATE INDEX IF NOT EXISTS idx_mail_audit_ai_queue_status
  ON mail_audit_ai_queue(status, created_at);
CREATE INDEX IF NOT EXISTS idx_mail_audit_ai_results_finding
  ON mail_audit_ai_results(finding_id);
CREATE INDEX IF NOT EXISTS idx_mail_audit_ai_results_disagree
  ON mail_audit_ai_results(agrees, confidence DESC);
