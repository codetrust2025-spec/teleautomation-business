"""A scheduled-interview claim the source cannot corroborate goes to review.

When the model proposes a transition the deterministic layer will not support,
`validate_result` refuses the transition. It also used to discard the whole
finding: `should_create_review_record=False`, no event, no notification,
nothing on any screen. Two mails from one genuine Zealogics AI interview --
"Interview Access Code - Azure Engineer at Zealogics" and "Reminder: Interview
Link Expires in 1 hour" -- were dropped exactly this way at confidence 1.0.

The refusal is kept, because the veto is usually right. Measured over the whole
production corpus, 51 proposals were refused for want of corroboration and most
were the veto catching a hallucination: SELECTED on a TCS "OTP for login",
HR_CONFIRMATION on "your Uber account is active", SELECTED on a GDPR retention
notice and on "New jobs posted" blasts. Surfacing all 51 would hand an operator
roughly 36 mails that are correctly ignored today.

Six of the 51 were a claim that a specific interview is scheduled or moved, and
all six were genuine. That is the group a silent drop actually costs something,
so that is the only group this promotes.

Nothing here books anything: the transition is still refused, the status is
still not an interview, and `backend_transition_validated` stays False, which
is what `should_route_to_mail_alert` requires of an OLLAMA result. These reach
the review queue, never the alert list.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent

ESTABLISHED = {"decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS"}
NOT_ESTABLISHED = {"decision": "NOT_ESTABLISHED", "message_kind": "PUBLIC_EVENT"}

# A mail whose body carries no assertion the deterministic layer will accept,
# so every proposal below is refused for want of corroboration.
MESSAGE = {
    "subject": "Interview Access Code - Azure Engineer at Zealogics",
    "body": "Hello, please use the access code to continue. Regards, the team.",
    "sender_name": "Zealogics", "sender_email": "support@zeaiq.zeasale.com",
    "recipient_email": "candidate@example.com", "sent_at": "2026-08-30T07:00:00+05:30",
}


def _result(status):
    classification = store._STATUS_CLASSIFICATION[status]
    interview = {key: None for key in (
        "date", "time", "end_time", "duration_minutes", "timezone",
        "mode", "round", "location", "meeting_link",
    )}
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True, "is_selection_or_offer_related": True,
        "should_create_review_record": True, "status": status,
        "classification": classification,
        "candidate_status": store._CLASSIFICATION_STATUS[classification],
        "confidence": 100, "ignore_reason": None,
        "candidate": {"name": None, "email": "candidate@example.com"},
        "company": {"name": "Zealogics", "domain": "zealogics.com"},
        "job": {"title": "Azure Engineer", "employment_type": None, "location": None},
        "recruiter": {"name": None, "email": None},
        "interview": interview,
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [],
        "evidence": [{"source": "EMAIL_BODY", "meaning": status,
                      "text": "please use the access code to continue"}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": "Access code for an interview.", "reason": "Access code.",
        "recommended_action": "Review.",
        "email_intent": "UNKNOWN", "document_type": "NONE",
        "is_candidate_specific": True, "is_job_outcome": True,
        "is_current_event": True, "is_questionnaire": False,
        "is_promotional_or_job_ad": False, "is_historical_information": False,
        "lifecycle_event": status, "business_domain": "INTERVIEW_TRACKING",
        "interview_event": "NONE", "evidence_summary": "Access code.",
    }


def validated(status, relevance=ESTABLISHED):
    value = _result(status)
    agent.validate_result(value, MESSAGE, [], relevance=relevance)
    return value


SCHEDULED = ["INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED"]
NOT_SCHEDULED = ["SELECTED", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE",
                 "CANDIDATE_REJECTED", "BACKGROUND_VERIFICATION"]


class TestAScheduledClaimSurvivesAsReview:
    @pytest.mark.parametrize("status", SCHEDULED)
    def test_it_becomes_needs_review(self, status):
        value = validated(status)
        assert value["status"] == "MANUAL_REVIEW_REQUIRED"
        assert value["classification"] == "needs_review"
        assert value["candidate_status"] == "Needs Review"

    @pytest.mark.parametrize("status", SCHEDULED)
    def test_an_operator_actually_sees_it(self, status):
        value = validated(status)
        assert value["should_create_review_record"] is True
        assert value["requires_manual_review"] is True
        assert value["ignore_reason"] is None

    @pytest.mark.parametrize("status", SCHEDULED)
    def test_it_says_what_could_not_be_corroborated(self, status):
        value = validated(status)
        assert "PROPOSAL_NOT_CORROBORATED" in value["risk_flags"]
        assert status in value["reason"]
        assert value["downgraded_from"] == status


class TestItIsStillNotABooking:
    @pytest.mark.parametrize("status", SCHEDULED)
    def test_the_transition_is_still_refused(self, status):
        value = validated(status)
        assert value["backend_transition_validated"] is False
        assert value["lifecycle_event"] == "NONE"
        assert value["interview_event"] == "NONE"
        assert value["business_domain"] == "NONE"

    @pytest.mark.parametrize("status", SCHEDULED)
    def test_the_reason_it_was_refused_is_still_recorded(self, status):
        value = validated(status)
        assert value["backend_validation_reason"].endswith("NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT")

    @pytest.mark.parametrize("status", SCHEDULED)
    def test_it_cannot_reach_the_alert_screen(self, status):
        """should_route_to_mail_alert demands a validated transition."""
        value = validated(status)
        value["classification_source"] = "OLLAMA"
        value["recruitment_relevance_result"] = dict(ESTABLISHED)
        event = {
            "primary_status": value["status"], "classification": value["classification"],
            "structured_result": value,
        }
        assert not store.should_route_to_mail_alert(
            event, {"classification": value["classification"]},
        )


class TestEverythingElseStaysQuiet:
    @pytest.mark.parametrize("status", NOT_SCHEDULED)
    def test_other_uncorroborated_proposals_are_still_ignored(self, status):
        """The veto catching a hallucination must not become operator work:
        SELECTED on an OTP mail, HR_CONFIRMATION on an account notice."""
        value = validated(status)
        assert value["status"] == "IGNORED_NOT_OFFER_RELATED"
        assert value["classification"] == "not_relevant"
        assert value["should_create_review_record"] is False
        assert value["requires_manual_review"] is False

    @pytest.mark.parametrize("status", SCHEDULED)
    def test_without_an_established_hiring_process_nothing_is_promoted(self, status):
        """A newsletter promising an interview guide proves nothing."""
        value = validated(status, relevance=NOT_ESTABLISHED)
        assert value["status"] == "IGNORED_NOT_OFFER_RELATED"
        assert value["should_create_review_record"] is False

    @pytest.mark.parametrize("status", SCHEDULED)
    def test_and_nothing_is_promoted_when_relevance_is_absent(self, status):
        value = validated(status, relevance=None)
        assert value["status"] == "IGNORED_NOT_OFFER_RELATED"
        assert value["should_create_review_record"] is False
