-- The review queue is for selection/offer outcomes that can trigger payment
-- follow-up, not every recruitment-related message. Archive old broad matches.
UPDATE ai_recruitment_events e
SET review_status='FALSE_POSITIVE',
    review_notes=COALESCE(NULLIF(review_notes,''),'Automatically archived by strict job-outcome filter v2.'),
    updated_at=now()
FROM mailbox_messages m
WHERE e.mailbox_message_id=m.id
  AND e.review_status='PENDING'
  -- Interview review is a first-class workflow now. This legacy
  -- selection/offer cleanup must never archive interview decisions.
  AND COALESCE(e.primary_status,'') NOT LIKE 'INTERVIEW_%'
  -- This legacy cleanup is rerun by ensure_schema(). Never reclassify events
  -- produced by the precision-first v3 pipeline on a later restart.
  AND e.prompt_version IS DISTINCT FROM 'v3'
  AND (
    e.primary_status NOT IN (
      'SELECTED','OFFER_INDICATION','OFFER_LETTER_RECEIVED','OFFER_ACCEPTED',
      'JOINING_CONFIRMED','JOINING_DATE_CHANGED','BACKGROUND_VERIFICATION',
      'DOCUMENT_VERIFICATION','MANUAL_REVIEW_REQUIRED'
    )
    OR (
      e.primary_status='MANUAL_REVIEW_REQUIRED'
      AND lower(COALESCE(m.subject,'')) !~
        '(selected|selection|offer|appointment|letter of intent|joining|onboarding|background verification|document verification)'
      AND NOT EXISTS (
        SELECT 1 FROM mailbox_attachments a
        WHERE a.mailbox_message_id=m.id
          AND lower(COALESCE(a.filename,'')) ~
            '(offer|appointment|joining|selection|loi).*(pdf|doc|docx)$'
      )
    )
  );

UPDATE mailbox_messages m
SET processing_status='IGNORED_NOT_JOB_OUTCOME', updated_at=now()
WHERE EXISTS (
  SELECT 1 FROM ai_recruitment_events e
  WHERE e.mailbox_message_id=m.id
    AND e.review_status='FALSE_POSITIVE'
    AND e.review_notes='Automatically archived by strict job-outcome filter v2.'
);
