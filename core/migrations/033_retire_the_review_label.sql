-- Retire the review label from events that are not pending anything.
--
-- There is no human review workflow any more: every validator exit now
-- resolves to a booking, an ignore, interview activity or an automatic retry.
-- The label outlived the workflow because `create_event` used to write
--
--     review_state = 'AUTO_VALIDATED' if validation_status=='AUTO_VALIDATED'
--                    else 'PENDING'
--
-- so every reading that was merely not auto-validated -- medium confidence, a
-- reconciled disagreement, interview activity with no bookable time -- was
-- filed as awaiting a person. 0b199cf replaced that with a flat 'AUTOMATED'
-- on both the create and reprocess paths, so the population is closed: this
-- clears what the old expression already wrote, and nothing refills it.
--
-- Measured on production 2026-09-11, against release a173246:
--
--     152  review_status = 'PENDING'
--      13  requires_manual_review = true
--      10  both
--     155  either
--       0  marked PENDING while also carrying a reviewed_at
--       0  claimed by promote_legacy_review_states, whose SELECT excludes
--          every automation_state these rows already hold
--
-- 67 of the pending rows are an INTERVIEW_CONFIRMED whose candidate row
-- already holds the confirmed slot. Those bookings happened and are correct;
-- only the label beside them is dead.
--
-- Why this does not hide real work. `summarize_selection_tracking_events` counts an
-- event as automation_pending when its automation_state or primary_status is
-- AI_RETRY_PENDING, or when review_status='PENDING' and validation_status is
-- NEEDS_REVIEW/RETRY_PENDING. 88 rows satisfy that third clause, and all 88
-- are already counted by one of the others -- the number that would vanish
-- from the metric is 0. The `PENDING_AI_REVIEW` candidate filter empties,
-- which is the point of retiring the label.
--
-- This touches the two review columns and nothing else. primary_status,
-- classification, interview_date, interview_time, structured_result,
-- validation_status, automation_state and every candidate and booking row are
-- left exactly as they are, so booking history is unchanged and the original
-- verdict stays readable for audit -- including inside structured_result,
-- which is what `recruitment_automation` actually reads when it derives an
-- automation state, so no automation decision moves.
--
-- `review_status` values that carry a real outcome are preserved: FALSE_POS
-- and IGNORED say what an operator decided and are not 'PENDING'. 'AUTOMATED'
-- is the value the codebase already uses here -- promote_legacy_review_states
-- writes exactly these two columns to exactly these two values.

UPDATE ai_recruitment_events
   SET review_status = 'AUTOMATED',
       updated_at = NOW()
 WHERE review_status = 'PENDING';

UPDATE ai_recruitment_events
   SET requires_manual_review = FALSE,
       updated_at = NOW()
 WHERE requires_manual_review = TRUE;
