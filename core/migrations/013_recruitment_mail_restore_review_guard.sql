-- The first interview recovery pass could restore an event that had already
-- been explicitly rejected. Rejected validation is terminal and must never
-- re-enter the human action queue.
UPDATE ai_recruitment_events
SET review_status='REJECTED',
    visible_in_offer_review=false,
    cleanup_version='interview_cleanup_restore_guard_v2',
    review_notes='Kept out of the action queue because this event was already rejected.',
    updated_at=now()
WHERE cleanup_version='interview_cleanup_restore_v1'
  AND review_status='PENDING'
  AND validation_status IN ('REJECTED','FALSE_POSITIVE');
