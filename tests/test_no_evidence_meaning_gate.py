"""A valid tracked classification is enough to raise a Mail Alert.

should_route_to_mail_alert used to take a second opinion on work Ollama had
already done: whenever an event needed review it also demanded that the model's
evidence `meaning` fields matched a fixed vocabulary. Models write those fields
as prose - "Request to complete pre-onboarding formalities", "Invitation -
Digital Employment BGV_18310757" - so correct classifications were withheld,
171 of them in production: flocareer interview reminders, an owlsure L1
discussion, an exazeit tech interview, the Innominds onboarding mail and the
digiverifier BGV invitation.

Nothing logged, because a withheld notification writes no row and raises no
error. That is what makes this class of gate dangerous rather than merely
strict.

Precision now lives entirely upstream of Ollama, where routing_decision and
job_board_notification exclude banking, job-board, promotional and service-ad
mail deterministically before any inference happens.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.recruitment_mail_store import should_route_to_mail_alert

TOMORROW = (date.today() + timedelta(days=3)).isoformat()
LAST_MONTH = (date.today() - timedelta(days=30)).isoformat()


def event(classification, *, meanings=("whatever the model wrote",),
          requires_review=True, interview_date=None, status=None):
    """An event as it reaches the notification step, review-flagged by default -
    the state in which the old gate did its blocking."""
    structured = {
        "evidence": [{"source": "EMAIL_BODY", "meaning": m, "text": m} for m in meanings],
        "requires_manual_review": requires_review,
        "classification": classification,
    }
    if interview_date:
        structured["interview"] = {"date": interview_date, "time": "02:00 PM",
                                   "timezone": "IST"}
    return (
        {"structured_result": structured, "primary_status": status or classification.upper(),
         "requires_manual_review": requires_review, "validation_status": "NEEDS_REVIEW"},
        {"classification": classification},
    )


class TestProseMeaningsNoLongerWithholdAnAlert:
    @pytest.mark.parametrize("classification,prose", [
        ("joining_confirmed", "Request to complete pre-onboarding formalities"),
        ("joining_confirmed", "Invitation - Digital Employment BGV_18310757"),
        ("offer_received", "The offer letter is attached for review"),
        ("job_selection_confirmed", "Candidate cleared the final round"),
    ])
    def test_selection_alerts_are_raised(self, classification, prose):
        row, analysis = event(classification, meanings=(prose,))
        assert should_route_to_mail_alert(row, analysis) is True

    @pytest.mark.parametrize("classification", [
        "interview_confirmed", "interview_rescheduled", "interview_shortlisted",
    ])
    def test_interview_alerts_are_raised(self, classification):
        row, analysis = event(classification, meanings=("some prose",),
                              interview_date=TOMORROW)
        assert should_route_to_mail_alert(row, analysis) is True

    def test_an_empty_evidence_list_no_longer_blocks(self):
        """The harshest case: the model returned no evidence meanings at all."""
        row, analysis = event("joining_confirmed", meanings=())
        assert should_route_to_mail_alert(row, analysis) is True

    def test_the_gate_is_gone_from_the_source(self):
        """Pins the removal itself, so a future edit cannot quietly reinstate a
        vocabulary check under another name."""
        import inspect

        from core import recruitment_mail_store as store

        src = inspect.getsource(store.should_route_to_mail_alert)
        assert "expected_meanings" not in src
        assert "IMPORTANT_ALERT_EVIDENCE_MEANINGS" not in src


class TestWhatStillHolds:
    def test_an_untracked_classification_raises_no_alert(self):
        row, analysis = event("needs_review")
        assert should_route_to_mail_alert(row, analysis) is False

    def test_a_past_interview_still_raises_no_alert(self):
        """A rescan must not reopen last month's interviews."""
        row, analysis = event("interview_confirmed", interview_date=LAST_MONTH)
        assert should_route_to_mail_alert(row, analysis) is False

    def test_an_explicitly_suppressed_historical_event_stays_suppressed(self):
        row, analysis = event("interview_confirmed", interview_date=TOMORROW)
        row["structured_result"]["_suppress_monitoring_notification"] = True
        assert should_route_to_mail_alert(row, analysis) is False

    def test_a_clean_auto_validated_event_still_alerts(self):
        row, analysis = event("offer_received", requires_review=False)
        row["validation_status"] = "AUTO_VALIDATED"
        assert should_route_to_mail_alert(row, analysis) is True
