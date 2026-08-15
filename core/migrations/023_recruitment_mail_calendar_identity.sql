-- Calendar identity for interview events.
--
-- A recruiter books one interview with two mails a minute apart: a covering
-- note from their own mailbox and the calendar invitation. Google then attaches
-- the invitation twice. Nothing already stored could relate those to each
-- other, because they differ in subject and in body, so one interview could
-- become three events.
--
-- The ICS UID is the calendar's own identity for the meeting and is stable
-- across all of them; SEQUENCE distinguishes a reschedule from a resend.

ALTER TABLE ai_recruitment_events
  ADD COLUMN IF NOT EXISTS calendar_uid text;
ALTER TABLE ai_recruitment_events
  ADD COLUMN IF NOT EXISTS calendar_sequence integer;

-- Looking up "has this candidate already got this meeting?" on every incoming
-- interview mail, so it needs to be cheap. Partial: only calendar-sourced rows
-- carry a UID at all.
CREATE INDEX IF NOT EXISTS idx_ai_recruitment_events_calendar_uid
  ON ai_recruitment_events(candidate_id, calendar_uid)
  WHERE calendar_uid IS NOT NULL;

-- The covering note has no UID, so it is matched on when and from whom. Same
-- lookup, different key.
CREATE INDEX IF NOT EXISTS idx_ai_recruitment_events_interview_slot
  ON ai_recruitment_events(candidate_id, interview_date, interview_time)
  WHERE interview_date IS NOT NULL;
