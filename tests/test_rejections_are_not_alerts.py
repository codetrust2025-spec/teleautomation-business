"""A rejection is recorded, not announced.

Mail Alerts is a queue of things needing attention. A rejection needs none: the
outcome is already final, there is nothing to action, and announcing it with a
sound and a counter buries the offers and interviews that do need someone. Two
rejections - Twilio and HackerRank - were sitting at the top of a seven-row
queue for exactly this reason.

Everything except the notification survives. The ai_recruitment_events row, the
candidate's status transition to Rejected, and candidate_status_history are all
still written, so the rejection remains visible wherever history is read.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core import recruitment_mail_store as store
from core.recruitment_mail_store import should_route_to_mail_alert

FUTURE = (date.today() + timedelta(days=3)).isoformat()


def event(classification, *, interview_date=None):
    structured = {
        "evidence": [{"source": "EMAIL_BODY", "meaning": classification.upper(),
                      "text": "evidence"}],
        "requires_manual_review": False,
        "classification": classification,
    }
    if interview_date:
        structured["interview"] = {"date": interview_date, "time": "02:00 PM",
                                   "timezone": "IST"}
    return (
        {"structured_result": structured, "primary_status": classification.upper(),
         "validation_status": "AUTO_VALIDATED", "confidence": 1.0},
        {"classification": classification},
    )


class TestRejectionsDoNotReachTheQueue:
    def test_a_rejection_creates_no_alert(self):
        row, analysis = event("candidate_rejected")
        assert should_route_to_mail_alert(row, analysis) is False

    def test_it_is_in_neither_filter_group(self):
        assert "candidate_rejected" not in store.SELECTION_RELATED_CLASSIFICATIONS
        assert "candidate_rejected" not in store.INTERVIEW_RELATED_CLASSIFICATIONS

    def test_it_is_not_tracked_so_it_cannot_reach_a_counter(self):
        """The summary cards and the table share this set, so absence here is
        what keeps the counters from moving."""
        assert "candidate_rejected" not in store.TRACKED_NOTIFICATION_CLASSIFICATIONS

    def test_the_dashboard_will_not_sound_for_it(self):
        """isTrackedMailAlert() reads these two lists; being in neither means
        playNotification is never called."""
        import pathlib
        import re

        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "dashboard" / "src" / "utils" / "mailAlertSound.js"
                  ).read_text(encoding="utf-8")
        for name in ("SELECTION_CLASSIFICATIONS", "INTERVIEW_BOOKING_CLASSIFICATIONS"):
            block = re.search(rf"export const {name} = \[(.*?)\]", source, re.S)
            assert block, f"{name} not found"
            assert "candidate_rejected" not in block.group(1)


class TestTheRestOfTheLifecycleIsUnaffected:
    @pytest.mark.parametrize("classification", [
        "job_selection_confirmed", "offer_received", "offer_accepted",
        "offer_declined", "offer_revoked", "joining_confirmed",
        "joining_date_updated", "onboarding_started", "background_verification",
        "document_verification", "compensation_confirmation",
        "final_round_cleared", "hr_confirmation",
    ])
    def test_every_other_selection_status_still_alerts(self, classification):
        row, analysis = event(classification)
        assert should_route_to_mail_alert(row, analysis) is True

    @pytest.mark.parametrize("classification", [
        "interview_shortlisted", "interview_confirmed",
        "interview_rescheduled", "interview_cancelled",
    ])
    def test_every_interview_status_still_alerts(self, classification):
        row, analysis = event(classification, interview_date=FUTURE)
        assert should_route_to_mail_alert(row, analysis) is True

    def test_the_two_groups_still_partition_the_tracked_set(self):
        """No classification may fall outside both filters - that is how
        interview_update became invisible."""
        assert (store.SELECTION_RELATED_CLASSIFICATIONS
                | store.INTERVIEW_RELATED_CLASSIFICATIONS
                ) == store.TRACKED_NOTIFICATION_CLASSIFICATIONS
        assert not (store.SELECTION_RELATED_CLASSIFICATIONS
                    & store.INTERVIEW_RELATED_CLASSIFICATIONS)

    def test_the_rejection_status_mapping_survives_for_history(self):
        """The candidate still becomes Rejected; only the alert is dropped."""
        assert store._STATUS_CLASSIFICATION["CANDIDATE_REJECTED"] == "candidate_rejected"
        assert store._CLASSIFICATION_STATUS["candidate_rejected"] == "Rejected"
