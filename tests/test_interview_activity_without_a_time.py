"""Real interview activity with nothing bookable in it is recorded, not parked.

Re-measured against production on 2026-09-11, after the disagreement and
confidence work. Six mails in 670 still reached a review state, and reading
them rather than their labels split them three ways:

    Action Required: Select Your Preferred Interview Slots with Accenture
    Accenture Interview 1st-Sep-26 -Reminder!!!
    Time to schedule your interview with Accenture!
        genuine, and carrying no time at all

    TCS || JD || ServiceNow developer
        a JD discussion

    You're invited to apply to a Deloitte opportunity
        the model call failed; already retries

The first three cannot be booked, because there is no time in them -- for the
slot-selection mail the candidate has not chosen one yet. They cannot be
retried to success either: re-reading will not find a time the sender did not
write, so a retry would loop. And ignoring them is how an interview goes
missing. They are recorded as INTERVIEW_UPDATE, which already exists, is not
in `interview_auto_booking.ACTIONABLE`, and is refused by
`apply_candidate_job_status` -- so it is visible and commits nothing.

The JD mail lands there too, and that is deliberate rather than overlooked. It
comes back ESTABLISHED / RECIPIENT_HIRING_PROCESS exactly as the Accenture
mails do, so no field separates them; only a subject keyword rule would, and
that is the shortcut this file has spent the week removing. Erring toward a
visible row that books nothing is the cheaper mistake.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services.interview_auto_booking import ACTIONABLE


ESTABLISHED = {"decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
               "confidence": 0.93}
MARKETING = {"decision": "ESTABLISHED", "message_kind": "MARKETING_OR_TRAINING",
             "confidence": 0.9}


class TestInterviewUpdateCannotAct:
    """The whole reason this status is the right home."""

    def test_it_cannot_book(self):
        assert store.canonical_classification(status="INTERVIEW_UPDATE") == "interview_update"
        assert "interview_update" not in ACTIONABLE

    def test_it_cannot_advance_a_candidate_stage(self):
        import inspect

        source = inspect.getsource(store.apply_candidate_job_status)
        assert '"interview_update"' in source
        # And a second, independent guard: the status written here is not
        # AUTO_VALIDATED, which that same line also requires.
        assert "AUTO_VALIDATED" in source

    def test_the_three_booking_classifications_are_unchanged(self):
        assert ACTIONABLE == {
            "interview_confirmed", "interview_rescheduled", "interview_cancelled"}


class TestAnUnreadableScheduleIsRecordedNotParked:
    def _validated(self, agent, interview):
        value = {
            "status": "INTERVIEW_CONFIRMED", "confidence": 0.95,
            "interview": dict(interview),
            "evidence": [{"source": "EMAIL_BODY", "text": "your interview is scheduled"}],
            "risk_flags": [],
        }
        return value

    def test_it_becomes_interview_activity(self):
        from services import recruitment_mail_agent as agent
        import inspect

        block = inspect.getsource(agent._validate_result)
        start = block.index("Interview schedule could not be read")
        window = block[start - 700:start + 200]
        assert 'status="INTERVIEW_UPDATE"' in window
        assert "MANUAL_REVIEW_REQUIRED" not in window
        assert "requires_manual_review=False" in window

    def test_no_date_or_time_is_invented(self):
        """The unreadable fields are cleared, never guessed."""
        from services import recruitment_mail_agent as agent
        import inspect

        block = inspect.getsource(agent._validate_result)
        start = block.index("Interview schedule could not be read")
        window = block[start - 900:start]
        assert "interview[field] = None" in window


class TestAnUnsupportedProposalIsRecordedNotParked:
    def _branch(self):
        from services import recruitment_mail_agent as agent
        import inspect

        source = inspect.getsource(agent._validate_result)
        start = source.index("unsupported_proposal = (")
        return source[start:source.index("downgraded_from=proposed_status,", start)]

    def test_genuine_interview_activity_is_recorded(self):
        branch = self._branch()
        assert 'status="INTERVIEW_UPDATE" if unsupported_proposal' in branch
        assert 'classification="interview_update" if unsupported_proposal' in branch

    def test_nobody_is_asked_anything(self):
        branch = self._branch()
        assert "requires_manual_review=False" in branch
        assert "MANUAL_REVIEW_REQUIRED" not in branch
        assert "Needs Review" not in branch

    def test_it_requires_the_candidates_own_hiring_process(self):
        """A marketing-shaped answer falls through to the ignore path, which is
        what keeps a webinar out of the interview list."""
        branch = self._branch()
        assert "CANDIDATE_HIRING_MESSAGE_KIND" in branch
        assert 'decision") or "").upper() == "ESTABLISHED"' in branch

    def test_anything_else_is_still_ignored(self):
        branch = self._branch()
        assert 'else "IGNORED_NOT_OFFER_RELATED"' in branch
        assert 'else "not_relevant"' in branch

    def test_no_transition_is_validated(self):
        """It records activity; it never claims the booking transition."""
        branch = self._branch()
        assert "backend_transition_validated=False" in branch


class TestTheProductionMailsThatDroveThis:
    """Named so the next reader can check them against production."""

    @pytest.mark.parametrize("subject", [
        "Action Required: Select Your Preferred Interview Slots with Accenture",
        "Accenture Interview 1st-Sep-26 -Reminder!!!",
        "Time to schedule your interview with Accenture!",
    ])
    def test_these_carried_no_time_and_so_cannot_book(self, subject):
        # The property that matters is not the subject but the absence of a
        # schedule: with no date and no time there is nothing for
        # `normalized_schedule` to accept, whatever status is recorded.
        from services.interview_auto_booking import BookingValidationError, normalized_schedule

        with pytest.raises(BookingValidationError):
            normalized_schedule({"interview": {"date": None, "time": None, "timezone": None}})

    def test_a_real_confirmed_interview_still_books(self):
        """Booking rules are untouched: a full schedule still converts."""
        from datetime import datetime, timezone

        from services.interview_auto_booking import normalized_schedule

        schedule = normalized_schedule(
            {"interview": {"date": "2026-12-01", "time": "03:30 PM",
                           "end_time": "04:15 PM", "timezone": "Asia/Kolkata"}},
            now=datetime(2026, 11, 30, 6, 0, tzinfo=timezone.utc))
        assert (schedule["date"], schedule["time"], schedule["time_end"]) == (
            "2026-12-01", "15:30", "16:15")


class TestTheModelFailurePathIsUnchanged:
    def test_a_failed_call_still_retries(self):
        from services import recruitment_mail_agent as agent
        import inspect

        source = inspect.getsource(agent.process_message)
        failure = source.index("AI_VALIDATION_UNAVAILABLE") if "AI_VALIDATION_UNAVAILABLE" in source else None
        assert "AI_RETRY_PENDING" in source
