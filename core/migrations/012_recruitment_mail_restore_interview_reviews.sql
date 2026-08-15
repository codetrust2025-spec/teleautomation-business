-- Restore strongly evidenced interview reviews that the legacy
-- selection/offer-only cleanup archived. The predicate is intentionally
-- narrow: only cleanup-generated false positives with an original interview
-- status, >=80% confidence, and source evidence are restored.
WITH restored AS (
  UPDATE ai_recruitment_events e
  SET primary_status=CASE
        WHEN COALESCE(e.original_primary_status,'') LIKE 'INTERVIEW_%'
          THEN e.original_primary_status
        ELSE e.structured_result->>'interview_event'
      END,
      review_status='PENDING',
      visible_in_offer_review=true,
      ignore_reason=NULL,
      ignored_at=NULL,
      cleanup_version='interview_cleanup_restore_v1',
      review_notes='Restored after legacy selection/offer cleanup incorrectly archived an interview review.',
      updated_at=now()
  WHERE e.cleanup_version IN ('selection_offer_precision_v3','offer_review_cleanup_v1')
    AND e.review_status IN ('FALSE_POSITIVE','IGNORED')
    AND e.validation_status IN ('NEEDS_REVIEW','RETRY_PENDING')
    AND (
      COALESCE(e.original_primary_status,'') LIKE 'INTERVIEW_%'
      OR COALESCE(e.structured_result->>'interview_event','') LIKE 'INTERVIEW_%'
    )
    AND e.confidence>=0.8
    AND jsonb_array_length(COALESCE(e.structured_result->'evidence','[]'::jsonb))>0
  RETURNING e.mailbox_message_id
)
UPDATE mailbox_messages m
SET processing_status='EVENT_CREATED',
    ignore_reason=NULL,
    ignored_at=NULL,
    cleanup_version='interview_cleanup_restore_v1',
    updated_at=now()
FROM restored r
WHERE r.mailbox_message_id=m.id;
