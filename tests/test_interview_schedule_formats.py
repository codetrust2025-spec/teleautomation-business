"""A schedule stated in an ordinary format must not be rejected for its shape.

Four Production interview mails sat in AI_RETRY_PENDING for weeks, climbing to
eight attempts each, because `validate_result` demanded one exact spelling of
the date and time. In every case the model classified INTERVIEW_CONFIRMED
correctly and quoted the source verbatim in its evidence:

| mail          | field | model returned              | source text                              |
| ------------- | ----- | --------------------------- | ---------------------------------------- |
| ValueMomentum | date  | 20-Jul-2026                 | "Date 20-Jul-2026 Time 03:00 PM"         |
| Cangra        | date  | 2026-07-30T12:00:00+05:30   | "Thu, 30 Jul 2026 12:00 PM IST"          |
| EY            | time  | 16:30 to 17:30              | "Time: 16:30 to 17:30"                   |
| Accenture     | time  | 12:00:00                    | "Time: 12:00:00 until 13:00:00 (24 Hours)"|

Identical input, identical failure, every retry — the interviews never
surfaced. The guard itself is kept: a source that states no date or no clock
time still fails, and an ambiguous value is still refused rather than guessed.
"""

import pytest

from services.recruitment_mail_agent import (
    _normalise_interview_date as norm_date,
    _recover_unambiguous_24_hour_time as recover_24h,
    _normalise_interview_time as norm_time,
)


# ── dates ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026-07-20", "2026-07-20"),                    # already ISO, untouched
    ("20-Jul-2026", "2026-07-20"),                   # ValueMomentum, live
    ("2026-07-30T12:00:00+05:30", "2026-07-30"),     # Cangra, live
    ("2026-07-30T12:00:00Z", "2026-07-30"),
    ("06th July 2026", "2026-07-06"),                # EY's evidence spelling
    ("20 July, 2026", "2026-07-20"),
    ("Jul 20, 2026", "2026-07-20"),
    ("July 20 2026", "2026-07-20"),
    ("3-Sept-2026", "2026-09-03"),
])
def test_unambiguous_dates_are_normalised_to_iso(raw, expected):
    assert norm_date(raw) == expected


@pytest.mark.parametrize("raw", [
    "", None, "TBD", "next Tuesday", "sometime in July", "2026-13-45",
    # All-numeric is genuinely ambiguous: 07/08 is 7 August to one sender and
    # 8 July to another. Guessing would book the wrong day.
    "07/08/2026", "07-08-2026", "08.07.2026",
    # A real month name with an impossible day is not a date.
    "31-Feb-2026",
])
def test_ambiguous_or_absent_dates_are_still_rejected(raw):
    assert norm_date(raw) == ""


# ── times stated on a 24-hour clock ─────────────────────────────────────────

def test_the_ey_evidence_quote_yields_the_stated_start():
    assert recover_24h([
        "L1 Interview | Frontend Developer - Lekkala Swathi | EY",
        "Date: 06th July 2026 Time: 16:30 to 17:30 Location: Virtually over Teams",
    ]) == "04:30 PM"


def test_the_accenture_slot_details_resolve_noon_not_midnight():
    """12:00 is ambiguous alone; "until 13:00:00" proves a 24-hour clock."""
    assert recover_24h([
        "Your Interview has been successfully Scheduled.",
        "Slot Details: Date: 17-08-2026 Time: 12:00:00 until 13:00:00 IST (24 Hours)",
    ]) == "12:00 PM"


@pytest.mark.parametrize("text,expected", [
    ("Interview scheduled for 17:00 hrs", "05:00 PM"),
    ("Please join the call at 13:05", "01:05 PM"),
    ("Slot: 23:45", "11:45 PM"),
    ("Starts 00:30", "12:30 AM"),
    # An hour of 1-11 is morning once the passage is proven 24-hour.
    ("Window is 09:30 to 18:00", "09:30 AM"),
])
def test_hours_with_one_possible_reading_are_taken_as_stated(text, expected):
    assert recover_24h([text]) == expected


@pytest.mark.parametrize("text", [
    "",
    "We will confirm the timing shortly",
    "The panel will call you",
    # 1-12 with no AM/PM and nothing proving a 24-hour clock stays refused.
    "Interview at 9:30 with the panel",
    "Please join at 11:00",
    "Call me on 12:15",
    # Not a clock at all.
    "Budget is 12.50 lakhs",
    "Version 10:30 of the document",
])
def test_a_source_without_an_unambiguous_time_recovers_nothing(text):
    assert recover_24h([text]) == ""


def test_an_am_pm_source_is_left_to_the_12_hour_normaliser():
    """Recovery only runs after the AM/PM path fails; it must not double-handle."""
    assert recover_24h(["Fri, August 14, 2:00 PM - 3:00 PM IST"]) == ""


# ── the original guards are untouched ───────────────────────────────────────

@pytest.mark.parametrize("raw", ["14:00", "09:30", "17:00", "14:00 - 15:00"])
def test_the_models_own_24_hour_value_is_still_rejected(raw):
    """Only source-verified text may supply a 24-hour time, never the model.

    `_normalise_interview_time` is what reads the model's own `interview.time`,
    and it stays strict — that separation is what stops a reformatted or
    invented value being trusted.
    """
    assert norm_time(raw) == ""


@pytest.mark.parametrize("raw,expected", [
    ("02:00 PM", "02:00 PM"), ("2:00PM", "02:00 PM"),
    ("2:00 PM - 3:00 PM IST", "02:00 PM"), ("3 PM", "03:00 PM"),
])
def test_the_12_hour_normaliser_still_behaves_exactly_as_before(raw, expected):
    assert norm_time(raw) == expected


# ── end to end through validate_result ──────────────────────────────────────

def _interview_result(evidence_text, **interview):
    """A schema-valid confirmed-interview row, shaped like the live pipeline's.

    `evidence_text` must appear verbatim in the message body — the evidence
    check runs before the schedule check and would otherwise reject the row
    first, which is exactly the guard that keeps invented quotes out.
    """
    from tests.test_recruitment_mail_agent import valid_result

    row = valid_result()
    row.update(
        status="INTERVIEW_CONFIRMED", classification="interview_confirmed",
        candidate_status="Interview Confirmed",
        summary="Interview confirmed", recommended_action="Attend.",
        evidence=[{"source": "EMAIL_BODY", "meaning": "INTERVIEW_CONFIRMED",
                   "text": evidence_text}],
    )
    schedule = dict(row["interview"])
    schedule.update(date="2026-07-20", time="03:00 PM", timezone="Asia/Kolkata")
    schedule.update(interview)
    row["interview"] = schedule
    return row


def test_validate_result_accepts_the_ey_shape_and_writes_both_back():
    from services.recruitment_mail_agent import validate_result
    quote = "06th July 2026 Time: 16:30 to 17:30"
    row = _interview_result(quote, date="06th July 2026", time="16:30 to 17:30")
    message = {"subject": "L1 Interview | Frontend Developer | EY",
               "body": f"Your L1 interview is scheduled for {quote} IST over Teams."}

    validate_result(row, message)

    assert row["interview"]["date"] == "2026-07-06"
    assert row["interview"]["time"] == "04:30 PM"


def test_validate_result_accepts_an_iso_timestamp_date():
    from services.recruitment_mail_agent import validate_result
    quote = "30 Jul 2026 at 12:00 PM IST"
    row = _interview_result(quote, date="2026-07-30T12:00:00+05:30", time="12:00 PM")
    message = {"subject": "Interview for Opus Tech",
               "body": f"Your interview is scheduled for {quote}."}
    validate_result(row, message)
    assert row["interview"]["date"] == "2026-07-30"


def test_validate_result_still_rejects_a_schedule_the_source_never_stated():
    """The guard that matters: nothing is invented when the source is silent.

    This used to raise, which also deleted the detection - an Accenture
    interview confirmation was parked and never surfaced. It now downgrades:
    the time is still not invented and booking still cannot fire, but a person
    gets to see the mail.
    """
    from services.recruitment_mail_agent import validate_result
    quote = "We will confirm the timing shortly"
    row = _interview_result(quote, time="sometime next week")
    message = {"subject": "Technical interview",
               "body": f"Your interview is scheduled. {quote}."}

    validate_result(row, message)
    assert row["interview"]["time"] is None, "a time absent from the source was invented"
    assert row["classification"] == "needs_review"
    assert row["requires_manual_review"] is True


def test_validate_result_still_rejects_an_ambiguous_numeric_date():
    """07/08/2026 is 7 August or 8 July. Still never guessed."""
    from services.recruitment_mail_agent import validate_result
    quote = "07/08/2026 at 03:00 PM IST"
    row = _interview_result(quote, date="07/08/2026")
    message = {"subject": "Technical interview",
               "body": f"Your interview is scheduled for {quote}."}
    validate_result(row, message)
    assert row["interview"]["date"] is None, "an ambiguous numeric date was guessed"
    assert row["classification"] == "needs_review"
