"""Reading a schedule the sender stated, and refusing to invent one they did not.

Two gaps, both found by measuring real production mail rather than by reading
the code.

**Relative wording had no anchor.** `_normalise_interview_date` understood every
explicit spelling and nothing else, so "tomorrow" and "this Thursday" resolved
to nothing at all. The payload has carried `email_date` the whole time; it was
simply never used. A relative phrase now resolves against the day the mail
arrived, and only against that -- with no anchor nothing is resolved, because a
relative date with no reference point is exactly the case that must reach a
human instead of being guessed.

**The timezone check only asked whether the field was non-empty.** "IST" was
stored verbatim and every consumer downstream had to guess again what it meant.
It is now resolved to a real zone -- IANA name, known abbreviation, or numeric
offset -- or refused.

Abbreviations resolve to a *place*, not to a fixed offset. `ZoneInfo("EST")`
does exist, but it is a fixed -05:00 with no daylight saving, while a recruiter
writing EST in July means -04:00. Mapping EST to America/New_York lets the
interview date decide the offset, which is the only way to get summer right.

Operations run on India time, so the sender's zone is kept only long enough to
convert: `normalized_schedule` turns the schedule into Asia/Kolkata and records
both the converted values and the zone they came from.

The population these were measured on matters. 77 of 91 confirmed interviews in
production carry an RFC5545 calendar invite and never reach the language model;
their schedule comes from the .ics and is exact. The 14 without one are where
the model does this work, and five of those were promotional mails that had been
given interview schedules appearing nowhere in them -- several dated to the
mail's own send date at a habitual 15:30.
"""

from __future__ import annotations

import pytest

from services.recruitment_mail_agent import (
    _normalise_interview_date as norm_date,
    _normalise_interview_timezone as norm_zone,
)


class TestRelativeWordingResolvesAgainstTheArrivalDay:
    """Real production mails, with the dates they actually carried."""

    def test_tomorrow_named_the_day_after_the_mail(self):
        # "Re: Interview details", sent 2026-08-12: "your interview is
        # scheduled for tomorrow, August 13, at 3:30 P.M"
        assert norm_date("tomorrow", "2026-08-12 09:15:00+00:00") == "2026-08-13"

    def test_today_named_the_day_the_mail_arrived(self):
        # "Reminder for interview today at 03:30 PM IST", sent 2026-08-26.
        assert norm_date("today", "2026-08-26 06:30:00+00:00") == "2026-08-26"

    @pytest.mark.parametrize("phrase,expected", [
        ("tonight", "2026-08-26"),
        ("this morning", "2026-08-26"),
        ("day after tomorrow", "2026-08-28"),
    ])
    def test_the_other_phrases_that_name_a_single_day(self, phrase, expected):
        assert norm_date(phrase, "2026-08-26 06:30:00+00:00") == expected

    def test_a_weekday_means_the_next_one_still_to_come(self):
        # 2026-08-26 is a Wednesday; the coming Thursday is the next day.
        assert norm_date("this Thursday", "2026-08-26 06:30:00+00:00") == "2026-08-27"

    def test_the_same_weekday_means_a_week_out_not_today(self):
        """A mail that arrives on Wednesday saying "Wednesday" cannot mean the
        day it was already sent on."""
        assert norm_date("Wednesday", "2026-08-26 06:30:00+00:00") == "2026-09-02"

    def test_next_weekday_adds_a_further_week(self):
        assert norm_date("next Thursday", "2026-08-26 06:30:00+00:00") == "2026-09-03"

    @pytest.mark.parametrize("phrase", ["coming Friday", "upcoming Friday", "Friday"])
    def test_the_qualifier_does_not_change_the_coming_day(self, phrase):
        assert norm_date(phrase, "2026-08-26 06:30:00+00:00") == "2026-08-28"

    def test_an_iso_date_still_wins_over_any_wording(self):
        assert norm_date("2026-09-01", "2026-08-26 06:30:00+00:00") == "2026-09-01"

    def test_a_named_month_date_still_wins(self):
        assert norm_date("20-Jul-2026", "2026-08-26 06:30:00+00:00") == "2026-07-20"


class TestNothingIsResolvedWithoutAnAnchor:
    """The existing contract: no arrival date, no guess."""

    @pytest.mark.parametrize("phrase", ["tomorrow", "today", "next Tuesday", "this Thursday"])
    def test_a_relative_phrase_alone_is_still_refused(self, phrase):
        assert norm_date(phrase) == ""
        assert norm_date(phrase, "") == ""
        assert norm_date(phrase, None) == ""

    @pytest.mark.parametrize("anchor", ["not a date", "TBD", "0000", "2026-13-45"])
    def test_an_unreadable_anchor_resolves_nothing(self, anchor):
        assert norm_date("tomorrow", anchor) == ""

    @pytest.mark.parametrize("phrase", ["TBD", "sometime next month", "soon", ""])
    def test_wording_that_names_no_day_is_still_refused(self, phrase):
        assert norm_date(phrase, "2026-08-26 06:30:00+00:00") == ""

    def test_an_all_numeric_date_is_still_ambiguous_and_refused(self):
        """07/08 is 7 August to one sender and 8 July to another. Having an
        anchor does not make it readable."""
        assert norm_date("07/08/2026", "2026-08-26 06:30:00+00:00") == ""


class TestTheTimezoneMustBeARealZone:
    @pytest.mark.parametrize("raw,expected", [
        ("Asia/Kolkata", "Asia/Kolkata"),
        ("IST", "Asia/Kolkata"),
        ("ist", "Asia/Kolkata"),
        ("  IST  ", "Asia/Kolkata"),
        ("Asia/Calcutta", "Asia/Kolkata"),
        ("UTC", "UTC"),
        ("GMT", "UTC"),
        ("America/New_York", "America/New_York"),
        # The model annotates the abbreviation with the zone, either way round.
        ("IST (Asia/Kolkata)", "Asia/Kolkata"),
        ("Asia/Kolkata (IST)", "Asia/Kolkata"),
    ])
    def test_a_zone_that_can_only_mean_one_place(self, raw, expected):
        assert norm_zone(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("EST", "America/New_York"), ("EDT", "America/New_York"),
        ("CST", "America/Chicago"), ("CDT", "America/Chicago"),
        ("MST", "America/Denver"), ("PST", "America/Los_Angeles"),
        ("PDT", "America/Los_Angeles"),
    ])
    def test_a_us_abbreviation_resolves_to_the_place_not_a_fixed_offset(self, raw, expected):
        """tzdata's own EST is a fixed -05:00 with no daylight saving, so a
        July interview written as EST would be booked an hour out. Resolving to
        the place lets the interview date decide the offset."""
        assert norm_zone(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("+05:30", "UTC+05:30"), ("-08:00", "UTC-08:00"), ("+0530", "UTC+05:30"),
        ("UTC+5:30", "UTC+05:30"), ("GMT-8", "UTC-08:00"), ("+00:00", "UTC+00:00"),
    ])
    def test_a_numeric_offset_is_accepted(self, raw, expected):
        assert norm_zone(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "   ", "TBD", "Not/AZone", "Mars/Olympus",
                                     "XYZ", "+19:00", "sometime"])
    def test_a_missing_or_unreal_zone_is_still_refused(self, raw):
        assert norm_zone(raw) == ""


def _confirmed_row(evidence_text, **interview):
    from tests.test_recruitment_mail_agent import valid_result

    row = valid_result()
    row.update(
        status="INTERVIEW_CONFIRMED", classification="interview_confirmed",
        candidate_status="Interview Confirmed", summary="Interview confirmed",
        recommended_action="Attend.",
        evidence=[{"source": "EMAIL_BODY", "meaning": "INTERVIEW_CONFIRMED",
                   "text": evidence_text}],
    )
    schedule = dict(row["interview"])
    schedule.update(date="2026-07-20", time="03:00 PM", timezone="Asia/Kolkata")
    schedule.update(interview)
    row["interview"] = schedule
    return row


class TestEndToEndThroughValidateResult:
    def test_a_relative_date_is_resolved_from_the_message(self):
        from services.recruitment_mail_agent import validate_result

        quote = "your interview is scheduled for tomorrow at 3:30 PM IST"
        row = _confirmed_row(quote, date="tomorrow", time="3:30 PM", timezone="IST")
        message = {"subject": "Re: Interview details", "body": f"Hi, {quote}.",
                   "sent_at": "2026-08-12 09:15:00+00:00"}

        validate_result(row, message)

        assert row["interview"]["date"] == "2026-08-13"
        assert row["classification"] == "interview_confirmed"

    def test_an_abbreviated_zone_is_stored_as_a_real_one(self):
        from services.recruitment_mail_agent import validate_result

        quote = "scheduled for 20-Jul-2026 at 3:00 PM IST"
        row = _confirmed_row(quote, date="20-Jul-2026", time="3:00 PM", timezone="IST")
        message = {"subject": "Interview", "body": f"Your interview is {quote}.",
                   "sent_at": "2026-07-18 09:15:00+00:00"}

        validate_result(row, message)

        assert row["interview"]["timezone"] == "Asia/Kolkata"

    def test_a_us_zone_is_kept_as_the_source_for_conversion(self):
        """Not rewritten to Asia/Kolkata here: the conversion needs the
        sender's own zone, and booking records both."""
        from services.recruitment_mail_agent import validate_result

        quote = "scheduled for 20-Jul-2026 at 9:00 AM EST"
        row = _confirmed_row(quote, date="20-Jul-2026", time="9:00 AM", timezone="EST")
        message = {"subject": "Interview", "body": f"Your interview is {quote}.",
                   "sent_at": "2026-07-18 09:15:00+00:00"}

        validate_result(row, message)

        assert row["interview"]["timezone"] == "America/New_York"
        assert row["classification"] == "interview_confirmed"

    def test_an_unresolvable_zone_sends_the_mail_to_a_human(self):
        from services.recruitment_mail_agent import validate_result

        quote = "scheduled for 20-Jul-2026 at 3:00 PM"
        row = _confirmed_row(quote, date="20-Jul-2026", time="3:00 PM", timezone="TBD")
        message = {"subject": "Interview", "body": f"Your interview is {quote}.",
                   "sent_at": "2026-07-18 09:15:00+00:00"}

        validate_result(row, message)

        assert row["classification"] == "interview_update"
        assert row["interview"]["timezone"] is None
        assert "INTERVIEW_SCHEDULE_UNREADABLE" in row["risk_flags"]

    def test_a_relative_date_with_no_sent_at_goes_to_review_not_a_guess(self):
        from services.recruitment_mail_agent import validate_result

        quote = "your interview is scheduled for tomorrow at 3:30 PM IST"
        row = _confirmed_row(quote, date="tomorrow", time="3:30 PM", timezone="IST")
        message = {"subject": "Re: Interview details", "body": f"Hi, {quote}."}

        validate_result(row, message)

        assert row["classification"] == "interview_update"
        assert row["interview"]["date"] is None


class TestEverythingIsBookedInIndiaTime:
    """Operations run on Asia/Kolkata, so conversion happens before booking.

    The stored values are what Daily Ops and Confirmed Slots render, so getting
    the conversion right here is what makes those screens show IST.
    """

    @staticmethod
    def _schedule(**interview):
        from services.interview_auto_booking import normalized_schedule

        row = {"interview": {"date": "2026-07-20", "time": "09:00 AM",
                             "timezone": "Asia/Kolkata", "end_time": None,
                             "duration_minutes": 30}}
        row["interview"].update(interview)
        return normalized_schedule(row, now=_early_2026())

    def test_a_us_morning_becomes_an_india_evening(self):
        # 09:00 in New York on 20 July is EDT (-04:00) -> 18:30 IST.
        booked = self._schedule(time="09:00 AM", timezone="EST")
        assert booked["date"] == "2026-07-20"
        assert booked["time"] == "18:30"

    def test_utc_is_converted_by_five_and_a_half_hours(self):
        booked = self._schedule(time="09:00 AM", timezone="UTC")
        assert booked["time"] == "14:30"

    def test_a_numeric_offset_is_converted(self):
        booked = self._schedule(time="09:00 AM", timezone="+00:00")
        assert booked["time"] == "14:30"

    def test_an_india_time_is_left_where_it_is(self):
        booked = self._schedule(time="03:00 PM", timezone="IST")
        assert booked["time"] == "15:00"

    def test_the_booking_zone_is_always_asia_kolkata(self):
        for zone in ("IST", "UTC", "EST", "PST", "+05:30", "America/Chicago"):
            assert self._schedule(time="09:00 AM", timezone=zone)["timezone"] == "Asia/Kolkata"

    @pytest.mark.parametrize("zone,expected", [
        ("IST", "Asia/Kolkata"), ("UTC", "UTC"), ("EST", "America/New_York"),
        ("PST", "America/Los_Angeles"), ("+05:30", "UTC+05:30"),
    ])
    def test_the_sender_zone_is_preserved_for_audit(self, zone, expected):
        assert self._schedule(time="09:00 AM", timezone=zone)["source_timezone"] == expected

    def test_the_stored_shapes_are_canonical(self):
        import re as _re

        booked = self._schedule(time="09:00 AM", timezone="UTC")
        assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}", booked["date"])
        assert _re.fullmatch(r"\d{2}:\d{2}", booked["time"])
        assert _re.fullmatch(r"\d{2}:\d{2}", booked["time_end"])

    @pytest.mark.parametrize("zone", ["", "TBD", "XYZ", "Mars/Olympus"])
    def test_no_booking_happens_until_the_zone_resolves(self, zone):
        from services.interview_auto_booking import BookingValidationError

        with pytest.raises(BookingValidationError) as caught:
            self._schedule(time="09:00 AM", timezone=zone)
        assert caught.value.code in {"MISSING_TIMEZONE", "INVALID_TIMEZONE"}


def _early_2026():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
