import pytest

from core.recruitment_offer_visibility import cleanup_reason, should_show_in_selection_offer_review


def event(status="MANUAL_REVIEW_REQUIRED", confidence=.85, subject="We are pleased to offer you the role", meaning="OFFER_INDICATION"):
    return {
        "primary_status": status, "confidence": confidence, "review_status": "PENDING",
        "visible_in_offer_review": True, "subject": subject,
        "structured_result": {
            "is_selection_or_offer_related": True,
            "evidence": [{"source": "EMAIL_SUBJECT", "meaning": meaning, "text": subject}],
        },
    }


def test_foundit_recommendation_zero_percent_is_archived():
    row = event(confidence=0, subject="Job recommendations for you | foundit (Monster)")
    row.update(sender_name="Sneha from foundit", summary="Recruitment-related email requires manual review.")
    assert should_show_in_selection_offer_review(row) is False
    assert cleanup_reason(row) == "JOB_RECOMMENDATION"


@pytest.mark.parametrize(("status", "subject", "meaning"), [
    ("SELECTED", "Congratulations, you have been selected for the Software Engineer role", "SELECTED"),
    ("OFFER_LETTER_RECEIVED", "Please find your offer letter attached", "OFFER_LETTER_RECEIVED"),
])
def test_genuine_selection_and_offer_are_preserved(status, subject, meaning):
    row = event(status=status, confidence=.95, subject=subject, meaning=meaning)
    assert should_show_in_selection_offer_review(row) is True
    assert cleanup_reason(row) is None


def test_interview_only_and_low_manual_review_are_hidden():
    assert cleanup_reason(event(subject="Interview scheduled for tomorrow", meaning="INTERVIEW")) == "INTERVIEW_OR_ASSESSMENT"
    assert should_show_in_selection_offer_review(event(confidence=.65)) is False


def test_manual_review_requires_strong_typed_evidence():
    assert should_show_in_selection_offer_review(event(confidence=.85)) is True
    weak = event(confidence=.85, subject="Generic recruiter update", meaning="RECRUITMENT")
    assert should_show_in_selection_offer_review(weak) is False


def test_timeout_only_manual_review_is_never_visible():
    row = event(confidence=0, subject="Update on your application", meaning="RECRUITMENT")
    row["validation_status"] = "RETRY_PENDING"
    row["structured_result"]["evidence"] = []
    assert should_show_in_selection_offer_review(row) is False


def test_explicit_ignored_states_never_show():
    row = event(status="SELECTED", confidence=.95, meaning="SELECTED")
    row["review_status"] = "IGNORED"
    assert should_show_in_selection_offer_review(row) is False
    row["review_status"] = "PENDING"
    row["visible_in_offer_review"] = False
    assert should_show_in_selection_offer_review(row) is False
