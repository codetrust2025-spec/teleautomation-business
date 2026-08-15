-- Lifecycle truth, canonical candidate identity, safe Ollama retry, and
-- audit-preserving correction of semantic-v3 false positives.

ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS canonical_candidate_id text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS validation_status text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS ai_status text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS email_intent text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS document_type text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS evidence_summary text;
ALTER TABLE ai_recruitment_events ADD COLUMN IF NOT EXISTS event_fingerprint text;

ALTER TABLE mail_ai_analyses ADD COLUMN IF NOT EXISTS ai_status text;
ALTER TABLE mail_ai_analyses ADD COLUMN IF NOT EXISTS validation_status text;
ALTER TABLE mail_ai_analyses ADD COLUMN IF NOT EXISTS email_intent text;
ALTER TABLE mail_ai_analyses ADD COLUMN IF NOT EXISTS document_type text;
ALTER TABLE mail_ai_analyses ADD COLUMN IF NOT EXISTS evidence_summary text;

ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS ai_retry_after timestamptz;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS ai_retry_count integer NOT NULL DEFAULT 0;
ALTER TABLE candidate_status_history ADD COLUMN IF NOT EXISTS validation_status text;
ALTER TABLE candidate_job_status ADD COLUMN IF NOT EXISTS validation_status text;

CREATE TABLE IF NOT EXISTS candidate_identity_links (
  alias_candidate_id text PRIMARY KEY,
  canonical_candidate_id text NOT NULL,
  match_method text NOT NULL,
  match_priority integer NOT NULL,
  verified boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Explicit profile relationships have the highest priority.
INSERT INTO candidate_identity_links(alias_candidate_id,canonical_candidate_id,match_method,match_priority,verified)
SELECT id, COALESCE(NULLIF(payload->>'canonical_candidate_id',''),NULLIF(payload->>'profile_candidate_id',''),id),
       'EXPLICIT_PROFILE_RELATIONSHIP',1,true
FROM candidates_store
WHERE COALESCE(NULLIF(payload->>'canonical_candidate_id',''),NULLIF(payload->>'profile_candidate_id','')) IS NOT NULL
ON CONFLICT(alias_candidate_id) DO UPDATE SET
  canonical_candidate_id=EXCLUDED.canonical_candidate_id,
  match_method=EXCLUDED.match_method,match_priority=EXCLUDED.match_priority,
  verified=EXCLUDED.verified,updated_at=now()
WHERE EXCLUDED.match_priority<candidate_identity_links.match_priority;

WITH identities AS (
  SELECT id, regexp_replace(COALESCE(payload->>'phone',''),'\D','','g') phone_key
  FROM candidates_store
), grouped AS (
  SELECT id, min(id) OVER(PARTITION BY phone_key) canonical_id
  FROM identities WHERE length(phone_key)>=8
)
INSERT INTO candidate_identity_links(alias_candidate_id,canonical_candidate_id,match_method,match_priority,verified)
SELECT id,canonical_id,'VERIFIED_PHONE',3,true FROM grouped
ON CONFLICT(alias_candidate_id) DO UPDATE SET
  canonical_candidate_id=EXCLUDED.canonical_candidate_id,
  match_method=EXCLUDED.match_method,match_priority=EXCLUDED.match_priority,
  verified=EXCLUDED.verified,updated_at=now()
WHERE EXCLUDED.match_priority<candidate_identity_links.match_priority;

WITH identities AS (
  SELECT id, lower(trim(COALESCE(payload->>'email',''))) email_key
  FROM candidates_store
), grouped AS (
  SELECT id, min(id) OVER(PARTITION BY email_key) canonical_id
  FROM identities WHERE email_key LIKE '%@%'
)
INSERT INTO candidate_identity_links(alias_candidate_id,canonical_candidate_id,match_method,match_priority,verified)
SELECT id,canonical_id,'VERIFIED_PERSONAL_EMAIL',4,true FROM grouped
ON CONFLICT(alias_candidate_id) DO UPDATE SET
  canonical_candidate_id=EXCLUDED.canonical_candidate_id,
  match_method=EXCLUDED.match_method,match_priority=EXCLUDED.match_priority,
  verified=EXCLUDED.verified,updated_at=now()
WHERE EXCLUDED.match_priority<candidate_identity_links.match_priority;

WITH grouped AS (
  SELECT candidate_id,
         min(candidate_id) OVER(PARTITION BY lower(trim(email_address))) canonical_id
  FROM candidate_mailboxes WHERE email_address LIKE '%@%'
)
INSERT INTO candidate_identity_links(alias_candidate_id,canonical_candidate_id,match_method,match_priority,verified)
SELECT candidate_id,canonical_id,'GMAIL_ACCOUNT_MAPPING',5,true FROM grouped
ON CONFLICT(alias_candidate_id) DO UPDATE SET
  canonical_candidate_id=EXCLUDED.canonical_candidate_id,
  match_method=EXCLUDED.match_method,match_priority=EXCLUDED.match_priority,
  verified=EXCLUDED.verified,updated_at=now()
WHERE EXCLUDED.match_priority<candidate_identity_links.match_priority;

INSERT INTO candidate_identity_links(alias_candidate_id,canonical_candidate_id,match_method,match_priority,verified)
SELECT id,id,'SELF',99,false FROM candidates_store
ON CONFLICT(alias_candidate_id) DO NOTHING;

UPDATE ai_recruitment_events e SET canonical_candidate_id=COALESCE(l.canonical_candidate_id,e.candidate_id)
FROM candidate_identity_links l WHERE l.alias_candidate_id=e.candidate_id
  AND e.canonical_candidate_id IS DISTINCT FROM COALESCE(l.canonical_candidate_id,e.candidate_id);
UPDATE ai_recruitment_events SET canonical_candidate_id=candidate_id WHERE canonical_candidate_id IS NULL;

UPDATE ai_recruitment_events SET
  validation_status=CASE
    WHEN review_status='APPROVED' THEN 'APPROVED'
    WHEN review_status='FALSE_POSITIVE' THEN 'FALSE_POSITIVE'
    WHEN review_status='DUPLICATE' THEN 'REJECTED'
    WHEN review_status IN('REJECTED','IGNORED') THEN 'REJECTED'
    WHEN COALESCE(structured_result->>'ai_validation_status','')='VALIDATED'
         AND NOT requires_manual_review THEN 'AUTO_VALIDATED'
    WHEN ai_model LIKE '%fallback:%' OR ai_model LIKE 'unavailable:%' THEN 'RETRY_PENDING'
    ELSE 'NEEDS_REVIEW'
  END,
  ai_status=CASE
    WHEN COALESCE(structured_result->>'ai_validation_status','')='VALIDATED' THEN 'ANALYZED'
    WHEN ai_model LIKE '%fallback:%' OR ai_model LIKE 'unavailable:%' THEN 'RETRY_PENDING'
    ELSE COALESCE(NULLIF(structured_result->>'ai_status',''),'ANALYZED')
  END,
  email_intent=COALESCE(email_intent,NULLIF(structured_result->>'email_intent','')),
  document_type=COALESCE(document_type,NULLIF(structured_result->>'document_type','')),
  evidence_summary=COALESCE(evidence_summary,NULLIF(structured_result->>'evidence_summary',''),summary),
  event_fingerprint=COALESCE(event_fingerprint,mailbox_message_id)
WHERE validation_status IS NULL OR ai_status IS NULL OR event_fingerprint IS NULL;

-- Archive obvious advertisements/questionnaires. Source messages and events
-- remain intact and every correction is recorded below.
WITH bad AS (
  SELECT DISTINCT e.id,e.candidate_id,e.primary_status,e.review_status
  FROM ai_recruitment_events e JOIN mailbox_messages m ON m.id=e.mailbox_message_id
  WHERE e.review_status='PENDING'
    AND e.primary_status IN('JOINING_CONFIRMED','JOINED')
    AND (
      lower(COALESCE(m.subject,'')) ~ '(^|[^a-z])job\s*\|'
      OR (
        lower(COALESCE(m.body_text,'')) LIKE '%current ctc%'
        AND lower(COALESCE(m.body_text,'')) LIKE '%expected ctc%'
        AND lower(COALESCE(m.body_text,'')) LIKE '%offer in hand%'
        AND lower(COALESCE(m.body_text,'')) LIKE '%date of joining%'
      )
    )
), audited AS (
  INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
  SELECT gen_random_uuid()::text,'system','system','SEMANTIC_V4_FALSE_POSITIVE',candidate_id,id,
         jsonb_build_object('primary_status',primary_status,'review_status',review_status),
         jsonb_build_object('primary_status','IGNORED_NOT_OFFER_RELATED','reason','RECRUITER_QUESTIONNAIRE_OR_JOB_ADVERTISEMENT'),now()
  FROM bad
  ON CONFLICT DO NOTHING RETURNING source_id
)
UPDATE ai_recruitment_events e SET
  original_primary_status=COALESCE(e.original_primary_status,e.primary_status),
  primary_status='IGNORED_NOT_OFFER_RELATED',review_status='FALSE_POSITIVE',
  validation_status='FALSE_POSITIVE',visible_in_offer_review=false,
  requires_manual_review=false,email_intent='RECRUITER_QUESTIONNAIRE',
  ignore_reason='RECRUITER_QUESTIONNAIRE_OR_JOB_ADVERTISEMENT',ignored_at=now(),
  cleanup_version='semantic_v4_lifecycle_truth',updated_at=now()
FROM bad WHERE bad.id=e.id;

-- A historical Date of Joining inside a payslip is employee metadata, not a
-- current joining event.
WITH bad AS (
  SELECT DISTINCT e.id,e.candidate_id,e.primary_status,e.review_status
  FROM ai_recruitment_events e
  JOIN mailbox_attachments a ON a.mailbox_message_id=e.mailbox_message_id
  LEFT JOIN mailbox_attachment_cache c ON c.checksum=a.checksum
  WHERE e.review_status='PENDING' AND e.primary_status IN('JOINING_CONFIRMED','JOINED')
    AND (
      lower(COALESCE(a.filename,'')) ~ '(pay\s*slip|payslip)'
      OR lower(COALESCE(c.extracted_text,'')) ~ '(pay\s*slip|payslip)\s+(for\s+)?the\s+month'
      OR (lower(COALESCE(c.extracted_text,'')) LIKE '%working days%'
          AND lower(COALESCE(c.extracted_text,'')) LIKE '%employee id%'
          AND lower(COALESCE(c.extracted_text,'')) LIKE '%date of joining%')
    )
), audited AS (
  INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
  SELECT gen_random_uuid()::text,'system','system','SEMANTIC_V4_FALSE_POSITIVE',candidate_id,id,
         jsonb_build_object('primary_status',primary_status,'review_status',review_status),
         jsonb_build_object('primary_status','IGNORED_NOT_OFFER_RELATED','reason','HISTORICAL_PAYSLIP'),now()
  FROM bad ON CONFLICT DO NOTHING RETURNING source_id
)
UPDATE ai_recruitment_events e SET
  original_primary_status=COALESCE(e.original_primary_status,e.primary_status),
  primary_status='IGNORED_NOT_OFFER_RELATED',review_status='FALSE_POSITIVE',
  validation_status='FALSE_POSITIVE',visible_in_offer_review=false,
  requires_manual_review=false,email_intent='EMPLOYMENT_DOCUMENT',document_type='PAYSLIP',
  ignore_reason='HISTORICAL_PAYSLIP',ignored_at=now(),
  cleanup_version='semantic_v4_lifecycle_truth',updated_at=now()
FROM bad WHERE bad.id=e.id;

-- Remaining unreviewed outage fallbacks are evidence awaiting semantic retry,
-- not lifecycle facts.
UPDATE ai_recruitment_events SET
  original_primary_status=COALESCE(original_primary_status,primary_status),
  primary_status='MANUAL_REVIEW_REQUIRED',classification='needs_review',
  candidate_status='Needs Review',validation_status='RETRY_PENDING',
  ai_status='RETRY_PENDING',requires_manual_review=true,
  structured_result=jsonb_set(
    jsonb_set(COALESCE(structured_result,'{}'::jsonb),'{lifecycle_event}','"NONE"'::jsonb,true),
    '{validation_status}','"RETRY_PENDING"'::jsonb,true
  ),
  summary='AI analysis could not complete. No lifecycle event was created; retry is pending.',
  evidence_summary='AI analysis could not complete. No lifecycle event was created; retry is pending.',
  updated_at=now()
WHERE review_status='PENDING'
  AND (ai_model LIKE '%fallback:%' OR ai_model LIKE 'unavailable:%')
  AND validation_status='RETRY_PENDING'
  AND primary_status<>'MANUAL_REVIEW_REQUIRED';

UPDATE mailbox_messages m SET processing_status='AI_RETRY_PENDING',ai_retry_after=now(),updated_at=now()
FROM ai_recruitment_events e
WHERE e.mailbox_message_id=m.id AND e.validation_status='RETRY_PENDING';

UPDATE offer_verification_cases c SET verification_status='IGNORED',updated_at=now()
FROM ai_recruitment_events e
WHERE c.ai_recruitment_event_id=e.id AND e.validation_status IN('FALSE_POSITIVE','REJECTED','RETRY_PENDING');

UPDATE candidate_status_history h SET validation_status=COALESCE(e.validation_status,'NEEDS_REVIEW')
FROM ai_recruitment_events e WHERE e.id=h.source_id AND h.validation_status IS NULL;

-- candidate_job_status is a derived current-state projection. Remove only AI
-- projections with no validated source; history and audit records are kept.
DELETE FROM candidate_job_status s
WHERE s.source='AI Mail Monitoring'
  AND NOT EXISTS (
    SELECT 1 FROM ai_recruitment_events e
    WHERE COALESCE(e.canonical_candidate_id,e.candidate_id)=s.candidate_id
      AND e.validation_status IN('AUTO_VALIDATED','APPROVED')
      AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE','REJECTED','IGNORED')
  );

WITH ranked AS (
  SELECT e.*,row_number() OVER(
    PARTITION BY COALESCE(e.canonical_candidate_id,e.candidate_id)
    ORDER BY CASE e.primary_status
      WHEN 'JOINED' THEN 90 WHEN 'POST_SELECTION_ONBOARDING' THEN 80
      WHEN 'JOINING_CONFIRMED' THEN 70 WHEN 'OFFER_ACCEPTED' THEN 60
      WHEN 'APPOINTMENT_LETTER_RECEIVED' THEN 50 WHEN 'OFFER_LETTER_RECEIVED' THEN 50
      WHEN 'OFFER_APPROVED' THEN 50 WHEN 'OFFER_IN_PROGRESS' THEN 50
      WHEN 'OFFER_INDICATION' THEN 50 WHEN 'FINAL_SELECTION_CONFIRMED' THEN 40
      WHEN 'SELECTED' THEN 40 ELSE 0 END DESC,e.created_at DESC
  ) rn
  FROM ai_recruitment_events e
  WHERE e.validation_status IN('AUTO_VALIDATED','APPROVED')
    AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE','REJECTED','IGNORED')
), current_truth AS (
  SELECT *,COALESCE(canonical_candidate_id,candidate_id) canonical_id,
    CASE primary_status
      WHEN 'JOINED' THEN 'Joined' WHEN 'POST_SELECTION_ONBOARDING' THEN 'Onboarding Started'
      WHEN 'JOINING_CONFIRMED' THEN 'Joining Confirmed' WHEN 'OFFER_ACCEPTED' THEN 'Offer Accepted'
      WHEN 'APPOINTMENT_LETTER_RECEIVED' THEN 'Offer Received' WHEN 'OFFER_LETTER_RECEIVED' THEN 'Offer Received'
      WHEN 'OFFER_APPROVED' THEN 'Offer Received' WHEN 'OFFER_IN_PROGRESS' THEN 'Offer Received'
      WHEN 'OFFER_INDICATION' THEN 'Offer Received' WHEN 'FINAL_SELECTION_CONFIRMED' THEN 'Selected'
      WHEN 'SELECTED' THEN 'Selected' ELSE 'Profile Active' END truth_status,
    CASE primary_status
      WHEN 'JOINED' THEN 90 WHEN 'POST_SELECTION_ONBOARDING' THEN 80 WHEN 'JOINING_CONFIRMED' THEN 70
      WHEN 'OFFER_ACCEPTED' THEN 60 WHEN 'APPOINTMENT_LETTER_RECEIVED' THEN 50
      WHEN 'OFFER_LETTER_RECEIVED' THEN 50 WHEN 'OFFER_APPROVED' THEN 50 WHEN 'OFFER_IN_PROGRESS' THEN 50
      WHEN 'OFFER_INDICATION' THEN 50 WHEN 'FINAL_SELECTION_CONFIRMED' THEN 40 WHEN 'SELECTED' THEN 40 ELSE 10 END truth_rank
  FROM ranked WHERE rn=1
)
INSERT INTO candidate_job_status(candidate_id,status,status_rank,source,source_id,gmail_message_id,classification,confidence,validation_status,updated_at)
SELECT canonical_id,truth_status,truth_rank,'AI Mail Monitoring',t.id,m.provider_message_id,t.classification,t.confidence,t.validation_status,now()
FROM current_truth t LEFT JOIN mailbox_messages m ON m.id=t.mailbox_message_id
ON CONFLICT(candidate_id) DO UPDATE SET
  status=EXCLUDED.status,status_rank=EXCLUDED.status_rank,source=EXCLUDED.source,
  source_id=EXCLUDED.source_id,gmail_message_id=EXCLUDED.gmail_message_id,
  classification=EXCLUDED.classification,confidence=EXCLUDED.confidence,
  validation_status=EXCLUDED.validation_status,updated_at=now();

CREATE UNIQUE INDEX IF NOT EXISTS idx_recruitment_event_fingerprint
  ON ai_recruitment_events(event_fingerprint) WHERE event_fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_recruitment_events_truth
  ON ai_recruitment_events(validation_status,primary_status,canonical_candidate_id);
CREATE INDEX IF NOT EXISTS idx_mailbox_messages_ai_retry
  ON mailbox_messages(processing_status,ai_retry_after)
  WHERE processing_status='AI_RETRY_PENDING';
