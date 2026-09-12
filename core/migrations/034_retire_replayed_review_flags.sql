-- Old ensure_schema() replayed historical data migrations after migration 033.
-- Retire only obsolete workflow flags on explicitly automated events, without
-- touching classification, source evidence, mail queues, candidates or slots.
-- Preserve actual operator decisions and record the previous flag values.
WITH stale AS (
  SELECT id,candidate_id,review_status,requires_manual_review
  FROM ai_recruitment_events
  WHERE automation_state IN ('AUTO_BOOK','AUTO_IGNORE','AI_RETRY_PENDING',
                            'AUTO_BOOKED','AUTO_CANCELLED','AUTO_RESCHEDULED')
    AND reviewed_at IS NULL
    AND (review_status='PENDING' OR requires_manual_review=true)
    AND review_status NOT IN ('FALSE_POSITIVE','FALSE_POS','REJECTED','IGNORED')
), audited AS (
  INSERT INTO recruitment_audit_log
    (id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
  SELECT gen_random_uuid()::text,'system','system','REPLAYED_REVIEW_FLAGS_RETIRED',
    candidate_id,id,
    jsonb_build_object('review_status',review_status,'requires_manual_review',requires_manual_review),
    jsonb_build_object('review_status','AUTOMATED','requires_manual_review',false,
                      'reason','LEGACY_UNTRACKED_MIGRATION_REPLAY'),now()
  FROM stale RETURNING source_id
)
UPDATE ai_recruitment_events e
SET review_status='AUTOMATED',requires_manual_review=false,updated_at=now()
FROM audited a WHERE e.id=a.source_id;
