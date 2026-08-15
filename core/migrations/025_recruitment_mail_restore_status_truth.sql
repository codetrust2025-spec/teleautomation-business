-- The interview-review restore (012) could label an event INTERVIEW_CONFIRMED
-- on a mail that states no interview at all.
--
-- Its CASE falls back to structured_result->>'interview_event' whenever
-- original_primary_status is not an INTERVIEW_* value. That field is a context
-- heuristic derived from wording, not the model's verdict, so a Naukri notice
-- reading only "The status of your job application on Naukri.com has been
-- updated" was restored as INTERVIEW_CONFIRMED while the model itself had
-- answered SELECTION_NEEDS_REVIEW. The result is an interview event with no
-- date and no time sitting in the review queue.
--
-- A heuristic may make a record visible; it must never outrank the model's own
-- answer about what the record IS. This restores the verdict the event was
-- built from, and leaves the row visible so the restore's real purpose -- not
-- losing the review -- still holds.
--
-- Idempotent: the rows it corrects are stamped with a new cleanup_version, so
-- a later startup no longer matches them.
UPDATE ai_recruitment_events
SET primary_status = original_primary_status,
    cleanup_version = 'interview_cleanup_restore_truth_v3',
    review_notes = COALESCE(review_notes, '')
      || ' Status restored to the model verdict; the interview label came from a wording heuristic, not the email.',
    updated_at = now()
WHERE cleanup_version = 'interview_cleanup_restore_v1'
  AND primary_status LIKE 'INTERVIEW_%'
  -- the model's own answer, which the restore overrode
  AND COALESCE(original_primary_status, '') NOT LIKE 'INTERVIEW_%'
  AND COALESCE(original_primary_status, '') <> ''
  AND COALESCE(structured_result->>'status', '') NOT LIKE 'INTERVIEW_%'
  AND COALESCE(structured_result->>'primary_status', '') NOT LIKE 'INTERVIEW_%'
  -- only where the label is unsupported by any schedule the mail actually gave
  AND interview_date IS NULL
  AND interview_time IS NULL;
