-- Candidate mail outcome audit.
--
-- A read-only, evidence-based reconstruction of what each connected candidate
-- mailbox actually received, held separately from the live detection pipeline.
-- Nothing here feeds candidate status automatically: findings are recorded,
-- reviewed, and only applied through an explicit administrator action.

CREATE TABLE IF NOT EXISTS mail_outcome_audit_runs (
  id text PRIMARY KEY,
  mode text NOT NULL DEFAULT 'REPORT_ONLY',
  scope text NOT NULL DEFAULT 'ALL',
  requested_by text,
  status text NOT NULL DEFAULT 'RUNNING',
  incremental boolean NOT NULL DEFAULT false,
  mailboxes_total integer NOT NULL DEFAULT 0,
  mailboxes_scanned integer NOT NULL DEFAULT 0,
  mailboxes_failed integer NOT NULL DEFAULT 0,
  messages_examined integer NOT NULL DEFAULT 0,
  findings_written integer NOT NULL DEFAULT 0,
  gaps_written integer NOT NULL DEFAULT 0,
  error_message text,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT mail_outcome_audit_runs_mode_check
    CHECK (mode IN ('REPORT_ONLY','APPLY_APPROVED')),
  CONSTRAINT mail_outcome_audit_runs_status_check
    CHECK (status IN ('RUNNING','COMPLETED','FAILED'))
);

-- One row per (mailbox, gmail message). The unique key is the idempotency
-- boundary: re-running the audit updates a finding in place instead of
-- creating a second outcome for the same mail.
CREATE TABLE IF NOT EXISTS mail_outcome_audit_findings (
  id text PRIMARY KEY,
  run_id text REFERENCES mail_outcome_audit_runs(id) ON DELETE SET NULL,
  mailbox_id text NOT NULL REFERENCES candidate_mailboxes(id) ON DELETE CASCADE,
  candidate_id text NOT NULL,
  canonical_candidate_id text NOT NULL,
  mailbox_message_id text REFERENCES mailbox_messages(id) ON DELETE CASCADE,
  provider_message_id text NOT NULL,
  provider_thread_id text,
  rfc_message_id text,
  calendar_uid text,
  attachment_fingerprint text,
  subject text,
  sender_name text,
  sender_email text,
  sender_domain text,
  received_at timestamptz,
  company_name text,
  company_domain text,
  job_title text,
  outcome text NOT NULL,
  outcome_rank integer NOT NULL DEFAULT 0,
  confidence double precision NOT NULL DEFAULT 0,
  rationale text,
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  attachment_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  authenticity text NOT NULL DEFAULT 'UNVERIFIED',
  authenticity_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  manual_review_required boolean NOT NULL DEFAULT false,
  pipeline_outcome text,
  pipeline_event_id text,
  pipeline_agreement text,
  content_signature text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT mail_outcome_audit_findings_unique
    UNIQUE (mailbox_id, provider_message_id)
);

-- Append-only trail of every outcome change for a finding. Required so an
-- outcome that moves (for example NEXT_ROUND upgraded to FINAL_SELECTION on a
-- later rescan) never silently overwrites its own history.
CREATE TABLE IF NOT EXISTS mail_outcome_audit_finding_history (
  id text PRIMARY KEY,
  finding_id text NOT NULL REFERENCES mail_outcome_audit_findings(id) ON DELETE CASCADE,
  run_id text,
  previous_outcome text,
  new_outcome text NOT NULL,
  previous_confidence double precision,
  new_confidence double precision,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Per-candidate rollup, rebuilt from findings on every run.
CREATE TABLE IF NOT EXISTS mail_outcome_audit_candidates (
  canonical_candidate_id text PRIMARY KEY,
  run_id text REFERENCES mail_outcome_audit_runs(id) ON DELETE SET NULL,
  candidate_id text NOT NULL,
  candidate_name text,
  mailbox_id text REFERENCES candidate_mailboxes(id) ON DELETE CASCADE,
  email_address text,
  monitoring_status text,
  connection_status text,
  last_successful_sync_at timestamptz,
  scan_status text NOT NULL DEFAULT 'SCANNED',
  scan_error text,
  messages_examined integer NOT NULL DEFAULT 0,
  relevant_messages integer NOT NULL DEFAULT 0,
  companies jsonb NOT NULL DEFAULT '[]'::jsonb,
  outcome_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
  strongest_outcome text NOT NULL DEFAULT 'NOT_RELEVANT',
  strongest_outcome_rank integer NOT NULL DEFAULT 0,
  strongest_finding_id text,
  strongest_confidence double precision NOT NULL DEFAULT 0,
  strongest_authenticity text,
  latest_outcome text,
  latest_outcome_at timestamptz,
  system_status text,
  system_status_source text,
  status_mismatch boolean NOT NULL DEFAULT false,
  mismatch_detail text,
  manual_review_required boolean NOT NULL DEFAULT false,
  conflicting_evidence boolean NOT NULL DEFAULT false,
  suspicious_evidence boolean NOT NULL DEFAULT false,
  recommended_action text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Pipeline gaps: mail the audit can see that TeleAutomation did not process,
-- or processed differently from what the evidence supports.
CREATE TABLE IF NOT EXISTS mail_outcome_audit_gaps (
  id text PRIMARY KEY,
  run_id text REFERENCES mail_outcome_audit_runs(id) ON DELETE SET NULL,
  mailbox_id text NOT NULL REFERENCES candidate_mailboxes(id) ON DELETE CASCADE,
  canonical_candidate_id text NOT NULL,
  provider_message_id text,
  mailbox_message_id text,
  gap_type text NOT NULL,
  severity text NOT NULL DEFAULT 'MEDIUM',
  detail text,
  audit_outcome text,
  pipeline_outcome text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Mailbox-level gaps (a failed sync, an incomplete backlog) carry no message
-- id. A plain UNIQUE would not deduplicate them, because NULL never equals
-- NULL in Postgres, so every audit run would append the same gap again.
CREATE UNIQUE INDEX IF NOT EXISTS mail_outcome_audit_gaps_unique
  ON mail_outcome_audit_gaps (mailbox_id, gap_type, COALESCE(provider_message_id, ''));

-- Explicit administrator approval of an audited outcome. The audit never
-- writes candidate status by itself; an approval row is the only bridge.
CREATE TABLE IF NOT EXISTS mail_outcome_audit_approvals (
  id text PRIMARY KEY,
  finding_id text REFERENCES mail_outcome_audit_findings(id) ON DELETE SET NULL,
  canonical_candidate_id text NOT NULL,
  requested_outcome text NOT NULL,
  previous_system_status text,
  applied_system_status text,
  decision text NOT NULL,
  approved_by text NOT NULL,
  notes text,
  applied boolean NOT NULL DEFAULT false,
  applied_at timestamptz,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT mail_outcome_audit_approvals_decision_check
    CHECK (decision IN ('APPROVED','REJECTED'))
);

-- Reply-To and Return-Path are authenticity inputs the original ingestion did
-- not persist. New mail records them; historical rows stay NULL and the audit
-- reports those checks as unavailable rather than as a pass.
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS reply_to_email text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS return_path_email text;

CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_candidate
  ON mail_outcome_audit_findings(canonical_candidate_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_outcome
  ON mail_outcome_audit_findings(outcome, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_review
  ON mail_outcome_audit_findings(manual_review_required, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_findings_thread
  ON mail_outcome_audit_findings(mailbox_id, provider_thread_id);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_gaps_type
  ON mail_outcome_audit_gaps(gap_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_candidates_outcome
  ON mail_outcome_audit_candidates(strongest_outcome, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_audit_runs_started
  ON mail_outcome_audit_runs(started_at DESC);
