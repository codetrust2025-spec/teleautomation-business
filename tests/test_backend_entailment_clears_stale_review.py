"""A veto belongs to the verdict that made it.

Karat sent "Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM
Project Is Coming Up!" carrying the date, the hour, the timezone and the
joining link. Both models read it as SELECTION_NEEDS_REVIEW /
interview_shortlisted at confidence 1.0 and set requires_manual_review, and
both wrote the sentence "No risk flags detected." into risk_flags.

`validate_result` then did what it is meant to do: the source text assertively
entails INTERVIEW_CONFIRMED, so it replaced the model's status, recording
backend_transition_validated=True and EXPLICIT_TRANSITION_ENTAILED. The block
doing that says in its own comment that the model must not veto an assertive
interview event recognised from the source -- but it left
requires_manual_review alone, and that boolean described the verdict it had
just replaced.

Downstream, validate_ai_for_booking reads exactly that field and refused the
booking as AI_REQUIRES_REVIEW. Traced in production: payment, duplicate,
conflict and slot checks were all NOT_CHECKED because the refusal came first.
With the field cleared the same stored result passes the gate, converts to
14:00-15:00 IST, passes payment, and collides with nothing.

Both causes are fixed here and both are needed: clearing the boolean alone is
undone further down by `bool(requires_manual_review or risk_flags)`, where the
prose sentence reads as a risk.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent

BODY = (
    "This is a quick reminder that your Altimetrik interview for the Citi Scaled "
    "Hiring FPC - NAM project is coming up soon! Your Interview "
    "Friday, September 11, 2026 from 8:30am to 9:30am UTC (+0000). "
    "Go To My Interview."
)
MESSAGE = {
    "subject": "Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM Project Is Coming Up!",
    "body": BODY, "sender_name": "Karat", "sender_email": "support@karat.io",
    "recipient_email": "candidate@example.com", "sent_at": "2026-09-09T08:30:05+00:00",
}
ESTABLISHED = {"decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS"}


def model_result(status="SELECTION_NEEDS_REVIEW", *, requires_review=True, risk_flags=None):
    """What both models actually returned for this mail.

    SELECTION_NEEDS_REVIEW is not in `_STATUS_CLASSIFICATION`; the model paired
    it with `interview_shortlisted` on its own, exactly as stored in production.
    """
    classification = store._STATUS_CLASSIFICATION.get(status, "interview_shortlisted")
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True, "is_selection_or_offer_related": True,
        "should_create_review_record": True, "status": status,
        "classification": classification,
        "candidate_status": store._CLASSIFICATION_STATUS.get(classification, "Needs Review"),
        "confidence": 100, "ignore_reason": None,
        "candidate": {"name": None, "email": "candidate@example.com"},
        "company": {"name": "Altimetrik", "domain": "altimetrik.com"},
        "job": {"title": "Engineer", "employment_type": None, "location": None},
        "recruiter": {"name": "Karat", "email": "support@karat.io"},
        "interview": {
            "date": "2026-09-11", "time": "08:30 AM", "end_time": "09:30 AM",
            "duration_minutes": 60, "timezone": "UTC", "mode": "Live Video Call",
            "round": "Technical Interview", "location": "Online", "meeting_link": None,
        },
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [],
        "evidence": [{
            "source": "EMAIL_BODY", "meaning": "INTERVIEW_CONFIRMED",
            "text": "Friday, September 11, 2026 from 8:30am to 9:30am UTC (+0000)",
        }],
        "risk_flags": ["No risk flags detected."] if risk_flags is None else risk_flags,
        "requires_manual_review": requires_review,
        "summary": "Interview reminder.", "reason": "Interview reminder.",
        "recommended_action": "Prepare.",
        "email_intent": "UNKNOWN", "document_type": "NONE",
        "is_candidate_specific": True, "is_job_outcome": True,
        "is_current_event": True, "is_questionnaire": False,
        "is_promotional_or_job_ad": False, "is_historical_information": False,
        "lifecycle_event": status, "business_domain": "INTERVIEW_TRACKING",
        "interview_event": "NONE", "evidence_summary": "Interview reminder.",
    }


def validated(**kwargs):
    value = model_result(**kwargs)
    agent.validate_result(value, MESSAGE, [], relevance=ESTABLISHED)
    return value


class TestTheRealMail:
    def test_the_backend_still_corrects_the_status(self):
        value = validated()
        assert value["status"] == "INTERVIEW_CONFIRMED"
        assert value["classification"] == "interview_confirmed"
        assert value["backend_transition_validated"] is True

    def test_the_stale_veto_no_longer_survives(self):
        value = validated()
        assert value["requires_manual_review"] is False
        assert value["manual_review_cleared_from"] == "SELECTION_NEEDS_REVIEW"

    def test_and_the_booking_gate_now_lets_it_through(self, monkeypatch):
        from services import interview_auto_booking as booking

        # Production has this on; the gate refuses everything without it.
        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
        value = validated()
        value["classification_source"] = "OLLAMA"
        value["ai_validation_status"] = "VALIDATED"
        booking.validate_ai_for_booking(value, value["classification"])

    def test_the_schedule_converts_to_the_right_ist_hour(self):
        from services import interview_auto_booking as booking

        value = validated()
        schedule = booking.normalized_schedule(value)
        assert schedule["time"] == "14:00"
        assert schedule["time_end"] == "15:00"
        assert schedule["timezone"] == "Asia/Kolkata"
        assert schedule["date"] == "2026-09-11"


class TestTheRiskFlagThatMeansNoRisk:
    def test_a_self_negating_flag_is_dropped(self):
        assert validated()["risk_flags"] == []

    @pytest.mark.parametrize("flag", [
        "No risk flags detected.", "No risk flags detected", "none - no risks",
        "No concerns identified", "No issues detected",
    ])
    def test_every_spelling_of_none_is_dropped(self, flag):
        assert validated(risk_flags=[flag])["risk_flags"] == []

    @pytest.mark.parametrize("flag", [
        "No specific interview schedule provided",
        "No explicit interview schedule provided.",
        "No Offer Detected",
        "No explicit selection or offer detected",
        "No explicit interview date, time, or timezone provided.",
    ])
    def test_a_prose_risk_naming_something_missing_is_kept(self, flag):
        """These say what is absent from the mail. They are real risks."""
        assert validated(risk_flags=[flag])["risk_flags"] == [flag]

    @pytest.mark.parametrize("flag", [
        "MODEL_DISAGREEMENT", "JOB_ADVERTISEMENT", "SPAM_OR_SCAM",
        "NO_DATE_TIME", "WORDING_STATUS_CONFLICT", "INTERVIEW_SCHEDULE_UNREADABLE",
    ])
    def test_code_shaped_flags_are_untouched(self, flag):
        assert flag in validated(risk_flags=[flag])["risk_flags"]


class TestWhatMustStillBlock:
    def test_a_model_that_agrees_on_the_status_keeps_its_veto(self):
        """Nothing was replaced, so the model's caution still stands."""
        value = validated(status="INTERVIEW_CONFIRMED", risk_flags=[])
        assert value["status"] == "INTERVIEW_CONFIRMED"
        assert value["requires_manual_review"] is True
        assert "manual_review_cleared_from" not in value

    def test_model_disagreement_still_blocks_the_transition(self):
        """It no longer asks a person, but it still accepts nothing.

        The outcome moved from review to automatic retry; what has not moved
        is that an unresolved disagreement validates no transition.
        """
        value = validated(risk_flags=["MODEL_DISAGREEMENT"])
        assert value["status"] == "AI_RETRY_PENDING"
        assert value["requires_manual_review"] is False
        assert value["backend_transition_validated"] is False
        assert value["backend_validation_reason"] == "MODEL_DISAGREEMENT"

    def test_a_real_risk_flag_still_forces_review(self):
        value = validated(requires_review=False, risk_flags=["SPAM_OR_SCAM"])
        assert value["requires_manual_review"] is True

    def test_the_model_flag_no_longer_vetoes_the_booking(self, monkeypatch):
        """The flag is kept on the result for audit, but the gate ignores it.

        Booking still rests on evidence: a validated source, confidence over
        the threshold, and an explicit schedule for a medium-confidence result.
        """
        from services import interview_auto_booking as booking

        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
        value = validated(status="INTERVIEW_CONFIRMED", risk_flags=[])
        assert value["requires_manual_review"] is True
        value["classification_source"] = "OLLAMA"
        value["ai_validation_status"] = "VALIDATED"
        booking.validate_ai_for_booking(value, value["classification"])

    def test_an_unsupported_status_is_not_promoted_at_all(self):
        """A mail whose source asserts nothing cannot reach the clearing path."""
        message = {**MESSAGE, "subject": "Newsletter", "body": "Interview tips this week."}
        value = model_result()
        agent.validate_result(value, message, [], relevance=ESTABLISHED)
        assert value["status"] != "INTERVIEW_CONFIRMED"
        assert value.get("manual_review_cleared_from") is None
