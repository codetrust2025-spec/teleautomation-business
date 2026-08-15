"""A shortlist has no interview date, and must not be hidden for lacking one.

INTERVIEW_SHORTLISTED shared INTERVIEW_CONFIRMED's "must carry a date" rule, so
every genuine shortlist was hidden: being shortlisted is the outcome, and the
interview is not scheduled yet. A confirmed interview still needs its date.
"""

from core.recruitment_offer_visibility import should_show_in_selection_offer_review


def _event(status, *, interview=None, confidence=0.85, meaning="INTERVIEW_SHORTLISTED"):
    return {
        "primary_status": status,
        "review_status": "PENDING",
        "confidence": confidence,
        "validation_status": "VALIDATED",
        "structured_result": {
            "is_selection_or_offer_related": True,
            "validation_status": "VALIDATED",
            "interview": interview or {},
            "evidence": [{"source": "EMAIL_BODY", "meaning": meaning,
                          "text": "provisionally shortlisted"}],
        },
    }


def test_a_shortlist_without_an_interview_date_is_visible():
    assert should_show_in_selection_offer_review(_event("INTERVIEW_SHORTLISTED")) is True


def test_a_shortlist_with_a_date_is_still_visible():
    event = _event("INTERVIEW_SHORTLISTED", interview={"date": "2026-08-20"})
    assert should_show_in_selection_offer_review(event) is True


def test_a_confirmed_interview_still_requires_a_date():
    assert should_show_in_selection_offer_review(_event("INTERVIEW_CONFIRMED")) is False
    with_date = _event("INTERVIEW_CONFIRMED", interview={"date": "2026-08-20"})
    assert should_show_in_selection_offer_review(with_date) is True


def test_low_confidence_is_still_hidden():
    assert should_show_in_selection_offer_review(
        _event("INTERVIEW_SHORTLISTED", confidence=0.4)) is False


def test_an_ignored_status_is_still_hidden():
    assert should_show_in_selection_offer_review(_event("IGNORED_NOT_OFFER_RELATED")) is False


def test_evidence_is_still_required():
    event = _event("INTERVIEW_SHORTLISTED")
    event["structured_result"]["evidence"] = []
    assert should_show_in_selection_offer_review(event) is False
