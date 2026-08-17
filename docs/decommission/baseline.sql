\pset pager off
\echo '== RETAINED TABLES (exact) =='
SELECT 'candidates_store'              AS table_name, count(*) FROM candidates_store
UNION ALL SELECT 'candidate_mailboxes',           count(*) FROM candidate_mailboxes
UNION ALL SELECT 'mailbox_messages',              count(*) FROM mailbox_messages
UNION ALL SELECT 'mailbox_attachments',           count(*) FROM mailbox_attachments
UNION ALL SELECT 'mail_monitoring_notifications', count(*) FROM mail_monitoring_notifications
UNION ALL SELECT 'ai_recruitment_events',         count(*) FROM ai_recruitment_events
UNION ALL SELECT 'mailbox_sync_jobs',             count(*) FROM mailbox_sync_jobs
UNION ALL SELECT 'mail_realtime_events',          count(*) FROM mail_realtime_events
UNION ALL SELECT 'recruitment_audit_log',         count(*) FROM recruitment_audit_log
ORDER BY 1;

\echo '== MAIL AUDIT TABLES - must be identical after deploy =='
SELECT 'mail_outcome_audit_runs'            AS table_name, count(*) FROM mail_outcome_audit_runs
UNION ALL SELECT 'mail_outcome_audit_findings',        count(*) FROM mail_outcome_audit_findings
UNION ALL SELECT 'mail_outcome_audit_finding_history', count(*) FROM mail_outcome_audit_finding_history
UNION ALL SELECT 'mail_outcome_audit_candidates',      count(*) FROM mail_outcome_audit_candidates
UNION ALL SELECT 'mail_outcome_audit_gaps',            count(*) FROM mail_outcome_audit_gaps
UNION ALL SELECT 'mail_outcome_audit_approvals',       count(*) FROM mail_outcome_audit_approvals
UNION ALL SELECT 'mail_outcome_audit_cleanup_log',     count(*) FROM mail_outcome_audit_cleanup_log
UNION ALL SELECT 'mail_audit_ai_queue',                count(*) FROM mail_audit_ai_queue
UNION ALL SELECT 'mail_audit_ai_results',              count(*) FROM mail_audit_ai_results
UNION ALL SELECT 'mail_audit_ai_log',                  count(*) FROM mail_audit_ai_log
ORDER BY 1;

\echo '== MIGRATION LEDGER =='
SELECT count(*) AS migrations_applied, max(applied_at) AS last_applied
FROM operations_schema_migrations;

\echo '== SCHEMA SIZE =='
SELECT count(*) AS tables FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE';
SELECT count(*) AS foreign_keys FROM pg_constraint WHERE contype='f';
