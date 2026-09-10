"""A trusted invitation books itself. No person is asked.

Gangadhar's ServiceNow interview was missed because the model contradicted
itself -- decision NOT_ESTABLISHED, kind RECIPIENT_HIRING_PROCESS -- and the
gate read that as a rejection. Making it a review request stopped the silence
but still waited for someone, and nobody came: the interview was that evening.

The invitation does not need the model to be decisive. Reaching the booking
branch already means `trusted_interview_result` accepted the .ics -- one RFC
5545 event, a UID, METHOD REQUEST or CANCEL, an organiser aligned with an
authenticated sender, this recipient present in ATTENDEE, an explicit start
with a timezone. That is what a booking rests on, and a model disagreeing with
itself does not weaken it.

What still stops a booking is unchanged and all downstream of detection:
payment, duplicate, conflict, lifecycle, and the confidence and completeness
gates in validate_ai_for_booking. What still stops a webinar is the model's
confident answer -- the Zoom workshop returns NOT_ESTABLISHED with PUBLIC_EVENT,
never RECIPIENT_HIRING_PROCESS, and is ignored before any of this.
"""

from __future__ import annotations

import inspect

import pytest

from services import interview_auto_booking as booking
from services import recruitment_mail_agent as agent


def relevance(decision, kind):
    return {
        "decision": decision, "message_kind": kind, "confidence": 0.85,
        "evidence": [{"source": "EMAIL_BODY", "text": "Microsoft Teams meeting"}],
        "reason": "from production",
    }


class TestTheGangadharShape:
    """decision and kind disagreeing, on a valid Teams invitation."""

    ANSWER = ("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS")

    def test_the_verdict_is_still_review(self):
        """The verdict names the disagreement; the branch decides what to do."""
        assert agent.calendar_invite_verdict(relevance(*self.ANSWER)) == "REVIEW"

    def test_the_branch_books_on_the_invitation(self):
        source = inspect.getsource(agent.process_message)
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        assert 'model, duration = calendar_result, "rfc5545-authenticated", 0' in block

    def test_no_human_decision_is_requested(self):
        source = inspect.getsource(agent.process_message)
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        for human in ("MANUAL_REVIEW_REQUIRED", "needs_review",
                      "requires_manual_review=True", "should_create_review_record=True"):
            assert human not in block

    def test_the_disagreement_is_still_recorded(self):
        """Booking on it is not the same as pretending it did not happen."""
        source = inspect.getsource(agent.process_message)
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        assert "calendar_intent_contradictory" in block
        assert 'calendar_result["recruitment_relevance_result"]' in block


class TestTheWebinarIsStillRefused:
    @pytest.mark.parametrize("kind", [
        "PUBLIC_EVENT",           # the Zoom workshop that was auto-booked in August
        "MARKETING_OR_TRAINING",  # GraphoTherapy, Talent500
        "NEWSLETTER",             # Naukri bootcamp
        "JOB_ADVERTISEMENT",
        "GENERAL", "UNKNOWN",
    ])
    def test_a_confident_non_candidate_answer_ignores(self, kind):
        assert agent.calendar_invite_verdict(relevance("NOT_ESTABLISHED", kind)) == "IGNORE"

    def test_the_ignore_branch_still_returns_without_booking(self):
        source = inspect.getsource(agent.process_message)
        block = source[source.index('elif verdict == "IGNORE"'):
                       source.index('else:\n            calendar_result = dict')]
        assert "CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW" in block
        assert "return None" in block


class TestNoHumanVetoRemainsInBooking:
    def test_the_model_flag_is_no_longer_read_by_the_gate(self):
        source = inspect.getsource(booking.validate_ai_for_booking)
        assert "AI_REQUIRES_REVIEW" not in source
        assert 'result.get("requires_manual_review")' not in source

    def test_a_flagged_result_now_books(self, monkeypatch):
        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
        value = {
            "classification_source": "OLLAMA", "ai_validation_status": "VALIDATED",
            "confidence": 0.96, "requires_manual_review": True,
            "interview": {"date": "2026-09-10", "time": "06:30 PM",
                          "timezone": "Asia/Kolkata"},
        }
        booking.validate_ai_for_booking(value, "interview_confirmed")


class TestEveryEvidenceGateSurvives:
    """Removing the human veto must not remove anything that reads evidence."""

    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")

    def _value(self, **over):
        value = {
            "classification_source": "OLLAMA", "ai_validation_status": "VALIDATED",
            "confidence": 0.96,
            "interview": {"date": "2026-09-10", "time": "06:30 PM",
                          "timezone": "Asia/Kolkata"},
        }
        value.update(over)
        return value

    def test_an_unvalidated_source_is_still_refused(self):
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(
                self._value(ai_validation_status="NEEDS_REVIEW"), "interview_confirmed")
        assert raised.value.args[0] == "AI_NOT_VALIDATED"

    def test_low_confidence_is_still_refused(self):
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(self._value(confidence=0.5), "interview_confirmed")
        assert raised.value.args[0] == "LOW_CONFIDENCE"

    def test_a_medium_confidence_result_still_needs_a_full_schedule(self):
        value = self._value(confidence=0.85, interview={"date": "", "time": "", "timezone": ""})
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(value, "interview_confirmed")
        assert raised.value.args[0] == "MEDIUM_CONFIDENCE_INCOMPLETE"

    def test_a_non_actionable_classification_is_still_refused(self):
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(self._value(), "not_relevant")
        assert raised.value.args[0] == "NOT_ACTIONABLE"

    def test_auto_booking_can_still_be_turned_off(self, monkeypatch):
        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "false")
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(self._value(), "interview_confirmed")
        assert raised.value.args[0] == "AUTO_BOOKING_DISABLED"

    def test_a_past_interview_is_still_refused(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        value = self._value(interview={"date": "2026-09-01", "time": "06:30 PM",
                                       "timezone": "Asia/Kolkata"})
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.normalized_schedule(
                value, now=datetime(2026, 9, 10, tzinfo=ZoneInfo("Asia/Kolkata")))
        assert raised.value.args[0] == "PAST_INTERVIEW"

    def test_the_payment_gate_is_untouched(self):
        assert callable(booking._payment_check)
        assert "payment" in inspect.getsource(booking._payment_check).lower()
