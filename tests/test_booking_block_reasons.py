"""A blocked booking must say why, in words an operator can act on."""
from __future__ import annotations

import pytest

from services import booking_block_reasons as reasons


@pytest.mark.parametrize(
    "internal_code,expected_code,expected_text",
    [
        ("DUPLICATE_BOOKING", "DUPLICATE_BOOKING",
         "Candidate already has a booking for this round"),
        ("SLOT_CONFLICT", "NO_MATCHING_SLOT",
         "No available slot matches the invite time"),
        ("INVALID_DATE", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("INVALID_TIME", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("MISSING_TIMEZONE", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("INVALID_TIMEZONE", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("INVALID_END_TIME", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("INVALID_DURATION", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("CROSS_DAY_INTERVIEW", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("MEDIUM_CONFIDENCE_INCOMPLETE", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("HISTORICAL_SCHEDULE_INCOMPLETE", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("INCOMPLETE_SCHEDULE", "MISSING_DATE_TIME",
         "Interview date or time could not be detected"),
        ("PAST_INTERVIEW", "PAST_INTERVIEW_DATE",
         "Interview date is in the past"),
        ("CANDIDATE_MAPPING_FAILED", "CANDIDATE_NOT_FOUND",
         "Candidate could not be identified"),
        ("BOOKING_NOT_FOUND", "ROUND_NOT_FOUND",
         "Interview round could not be identified"),
        ("BOOKING_AMBIGUOUS", "ROUND_NOT_FOUND",
         "Interview round could not be identified"),
        ("LOW_CONFIDENCE", "LOW_CONFIDENCE",
         "AI confidence is below the required threshold"),
        ("MISSING_EVIDENCE", "INCOMPLETE_EVIDENCE",
         "Invite screenshot or email details are incomplete"),
        ("AI_NOT_VALIDATED", "INCOMPLETE_EVIDENCE",
         "Invite screenshot or email details are incomplete"),
        ("PAYMENT_VALIDATION_FAILED", "PAYMENT_NOT_CLEARED",
         "Payment is not cleared for this interview"),
        ("AI_REQUIRES_REVIEW", "MANUAL_REVIEW_REQUIRED",
         "Booking requires manual review"),
        ("AUTO_BOOKING_DISABLED", "MANUAL_REVIEW_REQUIRED",
         "Booking requires manual review"),
        ("NOT_ACTIONABLE", "MANUAL_REVIEW_REQUIRED",
         "Booking requires manual review"),
    ],
)
def test_every_blocking_code_has_a_reason_an_operator_can_act_on(
    internal_code, expected_code, expected_text
):
    described = reasons.describe(internal_code)
    assert described["reason_code"] == expected_code
    assert described["reason"].startswith(expected_text)
    # The exact validator branch survives the translation.
    assert described["internal_code"] == internal_code


def test_an_unmapped_code_falls_back_to_manual_review():
    # A block nobody has classified still needs a person to look at it.
    described = reasons.describe("SOME_NEW_VALIDATOR_BRANCH")
    assert described["reason_code"] == "MANUAL_REVIEW_REQUIRED"
    assert described["reason"] == "Booking requires manual review"
    assert described["internal_code"] == "SOME_NEW_VALIDATOR_BRANCH"


def test_a_missing_code_never_produces_an_empty_reason():
    for value in (None, "", "   "):
        described = reasons.describe(value)
        assert described["reason"] == "Booking requires manual review"
        assert described["reason_code"] == "MANUAL_REVIEW_REQUIRED"


def test_the_reason_names_the_time_the_invite_asked_for():
    # "No available slot matches" is only actionable once it says which time.
    described = reasons.describe(
        "SLOT_CONFLICT", schedule={"date": "2026-08-03", "time": "16:30"},
    )
    assert described["reason"] == (
        "No available slot matches the invite time (3 Aug 2026, 4:30 PM)"
    )


def test_the_raw_ai_schedule_is_used_when_validation_never_normalized_one():
    # A schedule that failed to parse leaves only the model's 12-hour reading.
    described = reasons.describe(
        "DUPLICATE_BOOKING", interview={"date": "2026-08-03", "time": "4:30 PM"},
    )
    assert described["reason"].endswith("(3 Aug 2026, 4:30 PM)")


def test_a_normalized_schedule_wins_over_the_raw_extraction():
    described = reasons.describe(
        "PAST_INTERVIEW",
        schedule={"date": "2026-08-03", "time": "16:30"},
        interview={"date": "2026-09-09", "time": "9:00 AM"},
    )
    assert "3 Aug 2026, 4:30 PM" in described["reason"]


def test_reasons_that_a_time_would_not_clarify_stay_plain():
    described = reasons.describe(
        "LOW_CONFIDENCE", schedule={"date": "2026-08-03", "time": "16:30"},
    )
    assert described["reason"] == "AI confidence is below the required threshold"


def test_an_unusable_schedule_leaves_the_reason_unqualified():
    for schedule in ({}, {"date": "not-a-date"}, {"date": "2026-13-40"}):
        described = reasons.describe("SLOT_CONFLICT", schedule=schedule)
        assert described["reason"] == "No available slot matches the invite time"


def test_a_date_without_a_usable_time_still_names_the_day():
    assert reasons.format_schedule("2026-08-03", None) == "3 Aug 2026"
    assert reasons.format_schedule("2026-08-03", "half past four") == "3 Aug 2026"
    assert reasons.format_schedule("2026-08-03", "99:99") == "3 Aug 2026"


@pytest.mark.parametrize(
    "time,expected",
    [
        ("00:05", "3 Aug 2026, 12:05 AM"),
        ("12:00", "3 Aug 2026, 12:00 PM"),
        ("23:59", "3 Aug 2026, 11:59 PM"),
        ("9:00 AM", "3 Aug 2026, 9:00 AM"),
        ("12:00 AM", "3 Aug 2026, 12:00 AM"),
        ("4:30 pm", "3 Aug 2026, 4:30 PM"),
    ],
)
def test_both_clock_formats_read_the_same_to_a_user(time, expected):
    assert reasons.format_schedule("2026-08-03", time) == expected


def test_every_reason_code_has_text():
    # A code without a sentence would surface as a blank row.
    for code in reasons._INTERNAL_TO_REASON.values():
        assert reasons.REASON_TEXT.get(code)


def test_no_validator_code_is_left_without_a_mapping():
    """A new block would otherwise silently read as "requires manual review",
    which is safe but tells an operator nothing. This caught INCOMPLETE_SCHEDULE
    only after it had already reached Production."""
    import pathlib
    import re

    source = pathlib.Path(__file__).resolve().parents[1] / "services" / "interview_auto_booking.py"
    text = source.read_text(encoding="utf-8")
    raised = set(re.findall(r'BookingValidationError\(\s*"([A-Z_]+)"', text))
    raised |= set(re.findall(r'"failure_code":\s*"([A-Z_]+)"', text))

    unmapped = sorted(raised - set(reasons._INTERNAL_TO_REASON))
    assert not unmapped, f"these blocking codes have no operator-facing reason: {unmapped}"
