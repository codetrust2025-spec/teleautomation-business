-- Precision-first selection/offer correction. This migration archives noise;
-- it intentionally does not delete source messages or AI events.
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS ignore_reason text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS ignored_at timestamptz;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS cleanup_version text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS ignore_reason text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS ignored_at timestamptz;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS cleanup_version text;
ALTER TABLE offer_verification_cases ADD COLUMN IF NOT EXISTS offer_case_key text;
CREATE UNIQUE INDEX IF NOT EXISTS idx_offer_cases_case_key ON offer_verification_cases(offer_case_key);

WITH noisy AS (
  SELECT e.id,e.mailbox_message_id,
    CASE
      WHEN lower(COALESCE(m.subject,'')) ~ '(job recommendation|recommended jobs|jobs matching|jobs for you|job alert|featured jobs|similar jobs)' THEN 'JOB_RECOMMENDATION'
      WHEN lower(COALESCE(m.subject,'')) ~ '(interview|technical round|hr round)' THEN 'INTERVIEW'
      WHEN lower(COALESCE(m.subject,'')) ~ '(assessment|coding test)' THEN 'ASSESSMENT'
      WHEN lower(COALESCE(m.subject,'')) ~ '(application received|thank you for applying|application submitted|application under review)' THEN 'APPLICATION_UPDATE'
      WHEN lower(COALESCE(m.subject,'')) ~ '(newsletter|upgrade|premium|profile viewed|resume viewed)' THEN 'MARKETING'
      WHEN lower(COALESCE(m.subject,'')) ~ '(not selected|regret to inform|rejection)' THEN 'REJECTION'
      ELSE 'NO_QUALIFIED_SELECTION_OR_OFFER_EVIDENCE'
    END AS reason
  FROM ai_recruitment_events e
  JOIN mailbox_messages m ON m.id=e.mailbox_message_id
  WHERE (e.review_status='PENDING' OR e.review_notes='Automatically archived by strict job-outcome filter v2.')
    AND COALESCE(e.primary_status,'') NOT LIKE 'INTERVIEW_%'
    AND e.cleanup_version IS DISTINCT FROM 'manual_content_audit_keep_v1'
    -- Migrations are applied idempotently at startup. Only clean legacy
    -- detections; v3 events have already passed the evidence/confidence gate.
    AND e.prompt_version IS DISTINCT FROM 'v3'
    AND (
      e.confidence<0.8
      OR e.primary_status NOT IN(
        'SELECTED','FINAL_SELECTION_CONFIRMED','OFFER_INDICATION','OFFER_IN_PROGRESS',
        'OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED',
        'OFFER_ACCEPTED','JOINING_CONFIRMED','JOINED','POST_SELECTION_ONBOARDING',
        'MANUAL_REVIEW_REQUIRED'
      )
      OR jsonb_array_length(COALESCE(e.structured_result->'evidence','[]'::jsonb))=0
      OR (e.primary_status='MANUAL_REVIEW_REQUIRED' AND e.confidence<0.8)
      OR lower(COALESCE(m.subject,'')) ~ '(job recommendation|recommended jobs|jobs matching|jobs for you|job alert|featured jobs|similar jobs|interview|assessment|coding test|thank you for applying|application received|newsletter|profile viewed|resume viewed|regret to inform|not selected)'
    )
)
UPDATE ai_recruitment_events e
SET review_status='FALSE_POSITIVE',ignore_reason=noisy.reason,ignored_at=now(),
    cleanup_version='selection_offer_precision_v3',
    review_notes=COALESCE(NULLIF(e.review_notes,''),'Archived by selection/offer precision cleanup v3.'),
    updated_at=now()
FROM noisy WHERE noisy.id=e.id;

UPDATE mailbox_messages m
SET processing_status='IGNORED_NOT_OFFER_RELATED',ignore_reason=e.ignore_reason,
    ignored_at=COALESCE(e.ignored_at,now()),cleanup_version='selection_offer_precision_v3',updated_at=now()
FROM ai_recruitment_events e
WHERE e.mailbox_message_id=m.id AND e.cleanup_version='selection_offer_precision_v3';

UPDATE offer_verification_cases c
SET verification_status='IGNORED',updated_at=now()
FROM ai_recruitment_events e
WHERE c.ai_recruitment_event_id=e.id AND e.cleanup_version='selection_offer_precision_v3';
