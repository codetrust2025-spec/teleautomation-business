"""A date the model mis-formatted must not delete the finding around it.

August 2026, found by rescanning every candidate mailbox: an Innominds
"Welcome aboard - please complete the pre-onboarding formalities" and a
digiverifier employment-BGV invitation both classified correctly and were then
thrown away by `validate_result`, which raised "invalid ISO date:
offer.offer_date" over an auxiliary field nothing downstream needed. The raise
put them on the deterministic-failure path, two retries returned the same
verdict, and they were parked. Neither ever reached Mail Alerts.

An Accenture "Your Interview has been successfully Scheduled" was lost the same
way through the interview branch.

The rule these tests hold: read what can be read, drop what cannot, and never
discard a classification over the formatting of a field beside it. What is not
forgiven is invention - an all-numeric date whose order is ambiguous is still
refused, and an interview whose schedule cannot be read still never reaches
auto-booking.
"""

from __future__ import annotations

import pytest

from services.recruitment_mail_agent import validate_result


def selection_result():
    """A well-formed selection detection, as the model returns one."""
    return {
        "schema_version": "selection_offer_event_v1", "is_recruitment_related": True,
        "is_selection_or_offer_related": True, "should_create_review_record": True,
        "status": "OFFER_INDICATION", "confidence": .95, "ignore_reason": None,
        "candidate": {"name": None, "email": None},
        "company": {"name": None, "domain": None},
        "job": {"title": None, "employment_type": None, "location": None},
        "recruiter": {"name": None, "email": None},
        "interview": {k: None for k in
                      ("date", "time", "timezone", "mode", "round", "location", "meeting_link")},
        "offer": {"offer_detected": True, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [],
        "evidence": [{"source": "EMAIL_BODY", "meaning": "OFFER_INDICATION",
                      "text": "we are pleased to offer you"}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": "Offer letter received.", "recommended_action": "Review.",
    }


MESSAGE = {"subject": "Offer letter", "body": "we are pleased to offer you the position."}


class TestABadOfferDateNoLongerDiscardsTheFinding:
    def test_an_unreadable_date_does_not_raise(self):
        """The exact failure that lost the Innominds onboarding mail."""
        row = selection_result()
        row["offer"]["offer_date"] = "as per offer letter"
        validate_result(row, MESSAGE)  # must not raise

    def test_the_classification_survives_intact(self):
        row = selection_result()
        row["offer"]["offer_date"] = "as per offer letter"
        validate_result(row, MESSAGE)
        assert row["status"] == "OFFER_INDICATION"
        assert row["is_selection_or_offer_related"] is True

    def test_the_unreadable_date_is_dropped_not_kept(self):
        """A field nothing could parse must not travel on as if it were a date."""
        row = selection_result()
        row["offer"]["joining_date"] = "immediately"
        validate_result(row, MESSAGE)
        assert row["offer"]["joining_date"] is None

    def test_dropping_a_date_is_recorded(self):
        row = selection_result()
        row["offer"]["joining_date"] = "immediately"
        validate_result(row, MESSAGE)
        assert "UNREADABLE_OFFER_DATE_JOINING_DATE" in row["risk_flags"]

    @pytest.mark.parametrize("raw,expected", [
        ("2026-08-27", "2026-08-27"),
        ("27-Aug-2026", "2026-08-27"),
        ("27 August 2026", "2026-08-27"),
        ("Aug 27, 2026", "2026-08-27"),
        ("2026-08-27T12:00:00+05:30", "2026-08-27"),
    ])
    def test_readable_spellings_are_normalised_to_iso(self, raw, expected):
        row = selection_result()
        row["offer"]["offer_date"] = raw
        validate_result(row, MESSAGE)
        assert row["offer"]["offer_date"] == expected

    def test_an_ambiguous_all_numeric_date_is_still_refused(self):
        """12/07/2026 is either 12 July or 7 December. Guessing books the wrong
        day, so it is dropped and flagged rather than interpreted."""
        row = selection_result()
        row["offer"]["joining_date"] = "12/07/2026"
        validate_result(row, MESSAGE)
        assert row["offer"]["joining_date"] is None
        assert "UNREADABLE_OFFER_DATE_JOINING_DATE" in row["risk_flags"]

    def test_a_good_date_raises_no_flag(self):
        row = selection_result()
        row["offer"]["offer_date"] = "2026-08-27"
        validate_result(row, MESSAGE)
        assert row["risk_flags"] == []


def interview_result():
    row = selection_result()
    row.update(status="INTERVIEW_CONFIRMED", classification="interview_confirmed")
    row["evidence"] = [{"source": "EMAIL_BODY", "meaning": "INTERVIEW_CONFIRMED",
                        "text": "your interview has been scheduled"}]
    row["interview"] = {"date": "2026-09-04", "time": "02:00 PM", "timezone": "IST",
                        "mode": None, "round": None, "location": None, "meeting_link": None}
    return row


INTERVIEW_MESSAGE = {"subject": "Your Interview has been successfully Scheduled",
                     "body": "your interview has been scheduled"}


class TestAnUnreadableScheduleDowngradesInsteadOfVanishing:
    def test_it_does_not_raise(self):
        """What lost the Accenture interview confirmation."""
        row = interview_result()
        row["interview"]["timezone"] = ""
        validate_result(row, INTERVIEW_MESSAGE)  # must not raise

    def test_the_finding_is_kept_for_a_human(self):
        row = interview_result()
        row["interview"]["timezone"] = ""
        validate_result(row, INTERVIEW_MESSAGE)
        assert row["status"] == "MANUAL_REVIEW_REQUIRED"
        assert row["should_create_review_record"] is True
        assert row["requires_manual_review"] is True

    def test_auto_booking_can_no_longer_pick_it_up(self):
        """execute_auto_booking fires on interview_confirmed / _rescheduled
        only. A schedule nobody could read must never reach it."""
        row = interview_result()
        row["interview"]["time"] = "sometime tomorrow"
        validate_result(row, INTERVIEW_MESSAGE)
        assert row["classification"] == "needs_review"
        assert row["classification"] not in {"interview_confirmed", "interview_rescheduled"}

    def test_the_unusable_field_is_cleared(self):
        row = interview_result()
        row["interview"]["time"] = "sometime tomorrow"
        validate_result(row, INTERVIEW_MESSAGE)
        assert row["interview"]["time"] is None

    def test_the_reason_names_what_could_not_be_read(self):
        row = interview_result()
        row["interview"]["timezone"] = ""
        validate_result(row, INTERVIEW_MESSAGE)
        assert "timezone" in row["reason"]
        assert "INTERVIEW_SCHEDULE_UNREADABLE" in row["risk_flags"]

    def test_a_complete_schedule_is_untouched(self):
        """The whole point is that good interviews still book automatically."""
        row = interview_result()
        validate_result(row, INTERVIEW_MESSAGE)
        assert row["classification"] == "interview_confirmed"
        assert row["status"] == "INTERVIEW_CONFIRMED"
        assert row["interview"]["date"] == "2026-09-04"
        assert row["interview"]["time"] == "02:00 PM"
