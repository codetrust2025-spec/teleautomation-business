"""A reminder about an interview asserts that the interview exists.

The Altimetrik/Karat mail reached the model once routing was fixed, and was
then rejected as PROPOSED_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT.

`validate_lifecycle_event` fails closed unless `asserted_transitions` proves the
proposed transition deterministically, and for this mail that set was *empty*.
The INTERVIEW_CONFIRMED vocabulary knew only the mails that arrange an
interview -- "scheduled", "confirmed", "arranged", "booked", or an invitation
to an L-round. An interview platform reminding a candidate about an interview
already in the diary writes none of those:

    Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM Project
    Is Coming Up!
    ... a quick reminder that your Altimetrik interview ... is coming up soon!
    Friday, September 11, 2026 from 8:30am to 9:30am UTC (+0000)

So whatever the model proposed could never be validated, and the mail was
ignored with an explicit date and time in it.

The subject and body here are the ones production stored.
"""

from __future__ import annotations

import pytest

from services import recruitment_semantics as rs


SUBJECT = (
    "Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM Project "
    "Is Coming Up!"
)
BODY = (
    "This is a quick reminder that your Altimetrik interview for the Citi "
    "Scaled Hiring FPC - NAM project is coming up soon! Your Interview: "
    "Friday, September 11, 2026 from 8:30am to 9:30am UTC (+0000). "
    "Please click the button above to go to your interview dashboard."
)
FULL_TEXT = f"{SUBJECT} {BODY}"


class TestTheRemindersAsserts:
    def test_the_real_mail_asserts_a_confirmed_interview(self):
        assert "INTERVIEW_CONFIRMED" in rs.asserted_transitions(FULL_TEXT)

    def test_it_entails_the_transition_directly(self):
        assert rs.evidence_entails_transition("INTERVIEW_CONFIRMED", FULL_TEXT) is True

    def test_the_deterministic_context_names_the_event(self):
        """The second half of the same gap.

        validate_interview_event needs the assertion *and* a matching
        `interview_event`, and those were computed by two separate vocabularies
        that had drifted apart -- so the assertion alone was not enough.
        """
        context = rs.classify_context(SUBJECT, BODY)
        assert context["interview_event"] == "INTERVIEW_CONFIRMED"
        assert context["business_domain"] == "INTERVIEW_TRACKING"

    def test_the_assertion_survives_interview_validation(self):
        """The gate that actually rejected it, driven by the real context."""
        context = rs.classify_context(SUBJECT, BODY)
        status, reason = rs.validate_interview_event("INTERVIEW_CONFIRMED", context)
        assert status == "INTERVIEW_CONFIRMED"
        assert reason is None

    @pytest.mark.parametrize("text", [
        "Reminder: your technical interview is on Friday.",
        "Your interview is coming up tomorrow at 2pm.",
        "Your upcoming HR interview is on 11 September 2026.",
        "A quick reminder that your L2 interview is coming up soon.",
    ])
    def test_other_reminder_phrasings_assert_too(self, text):
        assert "INTERVIEW_CONFIRMED" in rs.asserted_transitions(text)

    def test_the_scheduling_vocabulary_still_works(self):
        """The patterns that already existed are untouched."""
        assert "INTERVIEW_CONFIRMED" in rs.asserted_transitions(
            "Your technical interview has been scheduled for 11 September."
        )


class TestItAssertsNothingItShouldNot:
    """Rejection and job-ad protection must be exactly as strong as before."""

    @pytest.mark.parametrize("text", [
        "Thank you for your interview. We regret to inform you your "
        "application was unsuccessful.",
        "Unfortunately you were not selected after your interview.",
    ])
    def test_a_rejection_never_asserts_a_booking(self, text):
        assert "INTERVIEW_CONFIRMED" not in rs.asserted_transitions(text)

    @pytest.mark.parametrize("text", [
        "Java Developer openings. Apply now. Interview rounds include "
        "technical and HR.",
        "We are hiring! 5 new jobs match your profile. Interview process "
        "is coming up in our hiring drive.",
    ])
    def test_a_job_advert_never_asserts_a_booking(self, text):
        assert "INTERVIEW_CONFIRMED" not in rs.asserted_transitions(text)

    @pytest.mark.parametrize("text", [
        "Your interview preparation guide is coming up in our next newsletter.",
        "Reminder: your interview tips newsletter is here.",
        "Your upcoming interview questions are listed below.",
        "Your interview checklist is coming up shortly.",
    ])
    def test_advice_about_interviews_is_not_an_interview(self, text):
        """The subject of "is coming up" is the guide, not the interview.

        Without the lookahead these asserted a booking -- a false positive the
        first version of this fix introduced.
        """
        assert "INTERVIEW_CONFIRMED" not in rs.asserted_transitions(text)

    @pytest.mark.parametrize("subject,body", [
        ("Interview outcome",
         "We regret to inform you your application was unsuccessful after your interview."),
        ("Java Developer jobs",
         "Apply now. Interview rounds include technical and HR."),
        ("Interview tips",
         "Your interview preparation guide is coming up in our newsletter."),
    ])
    def test_none_of_them_produce_an_interview_event(self, subject, body):
        """The deterministic event stays NONE, so nothing can be booked."""
        assert rs.classify_context(subject, body)["interview_event"] == "NONE"

    def test_someone_elses_meeting_is_not_this_candidates_interview(self):
        assert "INTERVIEW_CONFIRMED" not in rs.asserted_transitions(
            "A reminder that the interview panel meets weekly."
        )

    def test_a_bare_mention_is_not_an_assertion(self):
        assert "INTERVIEW_CONFIRMED" not in rs.asserted_transitions(
            "We conduct interviews for many roles."
        )


class TestTheOtherTransitionsAreUnchanged:
    """A reminder must not start asserting cancellations or reschedules."""

    def test_a_reminder_does_not_assert_a_cancellation(self):
        assert "INTERVIEW_CANCELLED" not in rs.asserted_transitions(FULL_TEXT)

    def test_a_reminder_does_not_assert_a_reschedule(self):
        assert "INTERVIEW_RESCHEDULED" not in rs.asserted_transitions(FULL_TEXT)

    def test_a_cancellation_still_asserts_itself(self):
        assert "INTERVIEW_CANCELLED" in rs.asserted_transitions(
            "Your interview has been cancelled."
        )

    def test_a_reschedule_still_asserts_itself(self):
        assert "INTERVIEW_RESCHEDULED" in rs.asserted_transitions(
            "Your interview has been rescheduled to Monday."
        )

    def test_a_cancelled_reminder_asserts_the_cancellation_too(self):
        """Both fire; which one wins is the model's proposal to make, and the
        validator then checks it against this set."""
        asserted = rs.asserted_transitions(
            "Reminder: your interview is coming up. It has now been cancelled."
        )
        assert "INTERVIEW_CANCELLED" in asserted
