-- Audit-safe historical cleanup for the Selection and Offer Review queue.
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS visible_in_offer_review boolean NOT NULL DEFAULT true;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS original_primary_status text;

WITH candidates AS (
  SELECT e.id,e.mailbox_message_id,
    CASE
      WHEN lower(COALESCE(m.subject,'')) ~ '(job recommendation|recommended jobs|jobs recommended for you|jobs matching your profile|new jobs for you|job alerts?|daily job alert|weekly job alert|similar jobs|suggested jobs|suggested opportunities|featured jobs|hiring alert|apply now|career opportunities|open positions|latest openings)' THEN 'JOB_RECOMMENDATION'
      WHEN lower(COALESCE(m.subject,'')) ~ '(interview|assessment|coding test)' THEN 'INTERVIEW_OR_ASSESSMENT'
      WHEN lower(COALESCE(m.subject,'')) ~ '(application received|application acknowledgement|thank you for applying|resume viewed|profile viewed)' THEN 'APPLICATION_UPDATE'
      WHEN lower(COALESCE(m.subject,'')||' '||COALESCE(m.sender_name,'')||' '||COALESCE(m.sender_email,'')) ~ '(foundit|monster|naukri|linkedin jobs|indeed|shine|timesjobs)' THEN 'JOB_PORTAL_PROMOTION'
      WHEN lower(COALESCE(m.subject,'')) ~ '(not selected|regret to inform|rejection|newsletter|marketing)' THEN 'NON_OFFER_RECRUITMENT_MAIL'
      ELSE 'NO_QUALIFIED_SELECTION_OR_OFFER_EVIDENCE'
    END reason
  FROM ai_recruitment_events e
  LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
  WHERE COALESCE(e.visible_in_offer_review,true)=true
    -- Interview events belong to the unified review queue but are outside the
    -- scope of this selection/offer cleanup.
    AND COALESCE(e.primary_status,'') NOT LIKE 'INTERVIEW_%'
    AND e.cleanup_version IS DISTINCT FROM 'manual_content_audit_keep_v1'
    AND e.review_status NOT IN('IGNORED','APPROVED')
    AND NOT (lower(COALESCE(m.subject,'')||' '||COALESCE(e.summary,'')||' '||COALESCE(e.structured_result::text,'')) ~
      '(you have been selected|selected for the role|selected for the position|selection confirmed|final selection|we are pleased to offer|we are delighted to offer|offer letter attached|employment offer|appointment letter|letter of appointment|offer approved|offer released|offer is being processed|joining date|date of joining|welcome aboard|employee onboarding|pre-joining formalities|report for joining)')
    AND (
      (e.primary_status='MANUAL_REVIEW_REQUIRED' AND e.confidence<0.8)
      OR e.primary_status NOT IN('SELECTED','FINAL_SELECTION_CONFIRMED','OFFER_INDICATION','OFFER_IN_PROGRESS','OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED','OFFER_ACCEPTED','JOINING_CONFIRMED','JOINED','POST_SELECTION_ONBOARDING','MANUAL_REVIEW_REQUIRED')
      OR jsonb_array_length(COALESCE(e.structured_result->'evidence','[]'::jsonb))=0
      OR (e.primary_status='MANUAL_REVIEW_REQUIRED' AND COALESCE((e.structured_result->>'is_selection_or_offer_related')::boolean,false)=false)
      OR lower(COALESCE(e.summary,'')) LIKE '%recruitment-related email requires manual review%'
      OR lower(COALESCE(m.subject,'')) ~ '(job recommendation|recommended jobs|jobs recommended for you|jobs matching your profile|new jobs for you|job alerts?|daily job alert|weekly job alert|similar jobs|suggested jobs|suggested opportunities|featured jobs|hiring alert|apply now|career opportunities|open positions|latest openings|interview|assessment|coding test|application received|thank you for applying|resume viewed|profile viewed|newsletter|regret to inform|not selected)'
    )
), updated AS (
  UPDATE ai_recruitment_events e SET
    original_primary_status=COALESCE(e.original_primary_status,e.primary_status),
    primary_status='IGNORED_NOT_OFFER_RELATED',review_status='IGNORED',
    visible_in_offer_review=false,ignore_reason=c.reason,ignored_at=COALESCE(e.ignored_at,now()),
    cleanup_version='offer_review_cleanup_v1',updated_at=now()
  FROM candidates c WHERE c.id=e.id
  RETURNING e.id,e.mailbox_message_id,e.ignore_reason
)
UPDATE mailbox_messages m SET processing_status='IGNORED_NOT_OFFER_RELATED',ignore_reason=u.ignore_reason,
  ignored_at=COALESCE(m.ignored_at,now()),cleanup_version='offer_review_cleanup_v1',updated_at=now()
FROM updated u WHERE u.mailbox_message_id=m.id;

UPDATE offer_verification_cases c SET verification_status='IGNORED',updated_at=now()
FROM ai_recruitment_events e WHERE c.ai_recruitment_event_id=e.id AND e.cleanup_version='offer_review_cleanup_v1';

UPDATE recruitment_review_flags f SET review_status='IGNORED'
FROM ai_recruitment_events e WHERE f.event_id=e.id AND e.cleanup_version='offer_review_cleanup_v1';

CREATE INDEX IF NOT EXISTS idx_recruitment_events_offer_review_visible
  ON ai_recruitment_events(created_at DESC) WHERE visible_in_offer_review=true;
