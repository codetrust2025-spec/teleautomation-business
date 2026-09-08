"""The Altimetrik reminder, from routing through to an IST slot.

    Subject: Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM
             Project Is Coming Up!
    From:    support@karat.io
    Body:    Friday, September 11, 2026 from 8:30am to 9:30am UTC (+0000)

It never reached the booking code at all. Production recorded it as
`IGNORED_NOT_OFFER_RELATED / NO_RECRUITMENT_ROUTING_SIGNAL`: it qualified
nowhere, because the prefilter wants a selection or offer signal and a reminder
is neither, "interview" is not one of the ambiguous cues, and
`recruiting_invite_signal` requires a job title in the subject -- which
"Citi Scaled Hiring FPC - NAM Project" is not.

So no analysis ran, no classification was made, and no booking was attempted.
The timezone handling underneath was never the problem and is checked here too,
because the report named it: UTC 8:30-9:30am is 14:00-15:00 IST, which is the
2:00 PM - 3:00 PM the reader expected.

The subject and body are the ones production stored, read from mailbox_messages.
"""

from __future__ import annotations

import pytest

from services import recruitment_mail_agent as agent
from services.interview_auto_booking import normalized_schedule


SUBJECT = (
    "Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM Project "
    "Is Coming Up!"
)
SENDER = "support@karat.io"
BODY = """This is a quick reminder that your Altimetrik interview for the Citi \
Scaled Hiring FPC - NAM project is coming up soon!

Your Interview
Friday, September 11, 2026 from 8:30am to 9:30am UTC (+0000)

GO TO MY INTERVIEW

Please click the button above to go to your interview dashboard. There you \
will find links to join your video call and access Karat Studio."""


class TestItIsRoutedForAnalysis:
    def test_the_exact_email_reaches_the_model(self):
        decision = agent.routing_decision(SUBJECT, BODY, "", SENDER)
        assert decision["send_to_ai"] is True
        assert decision["reason"] == "SCHEDULED_INTERVIEW"

    def test_the_signal_fires_on_its_own(self):
        assert agent.scheduled_interview_signal(SUBJECT, BODY, SENDER) is True

    def test_the_subject_alone_is_enough_when_it_carries_both(self):
        assert agent.scheduled_interview_signal(
            "Interview on September 11, 2026 at 8:30am", "", SENDER,
        ) is True

    def test_it_reads_an_attachment_too(self):
        assert agent.scheduled_interview_signal(
            "Reminder", "", SENDER,
            [{"filename": "invite.ics", "text": "Interview 2026-09-11 08:30"}],
        ) is True


class TestItStillFailsClosed:
    """Two signals are required together. Either alone is ordinary mail."""

    def test_saying_interview_without_a_time_is_not_enough(self):
        assert agent.scheduled_interview_signal(
            "Interview tips", "Read our guide to interview preparation.", "",
        ) is False

    def test_a_date_and_time_without_an_interview_is_not_enough(self):
        assert agent.scheduled_interview_signal(
            "Team offsite", "Friday, September 11, 2026 from 8:30am to 9:30am", "",
        ) is False

    def test_a_rejection_is_not_a_booking(self):
        assert agent.scheduled_interview_signal(
            "Interview outcome",
            "Thank you for your interview. We will not be moving forward.", "",
        ) is False

    def test_a_job_advert_is_not_a_booking(self):
        assert agent.scheduled_interview_signal(
            "Java Developer jobs for you", "5 new jobs match your profile.", "",
        ) is False

    def test_an_empty_message_is_not_a_booking(self):
        assert agent.scheduled_interview_signal("", "", "") is False

    def test_it_runs_after_the_deterministic_noise_filters(self):
        """A conclusive noise verdict above must still win."""
        import inspect

        source = inspect.getsource(agent.routing_decision)
        assert source.index("job_advertisement_digest") < source.index(
            "scheduled_interview_signal"
        )
        assert source.index("semantic_context") < source.index(
            "scheduled_interview_signal"
        )


class TestTheScheduleConvertsToIst:
    """The report named the timezone, so it is pinned even though it was right."""

    def test_utc_becomes_ist(self):
        schedule = normalized_schedule({"interview": {
            "date": "2026-09-11", "time": "08:30 AM",
            "end_time": "09:30 AM", "timezone": "UTC",
        }})
        assert schedule["date"] == "2026-09-11"
        assert schedule["time"] == "14:00"
        assert schedule["time_end"] == "15:00"
        assert schedule["source_timezone"] == "UTC"

    def test_the_offset_annotation_is_understood(self):
        """The mail writes it as "UTC (+0000)"."""
        schedule = normalized_schedule({"interview": {
            "date": "2026-09-11", "time": "08:30 AM",
            "end_time": "09:30 AM", "timezone": "UTC (+0000)",
        }})
        assert (schedule["time"], schedule["time_end"]) == ("14:00", "15:00")

    def test_an_hour_stays_an_hour(self):
        schedule = normalized_schedule({"interview": {
            "date": "2026-09-11", "time": "08:30 AM",
            "end_time": "09:30 AM", "timezone": "UTC",
        }})
        start = tuple(int(part) for part in schedule["time"].split(":"))
        end = tuple(int(part) for part in schedule["time_end"].split(":"))
        assert (end[0] * 60 + end[1]) - (start[0] * 60 + start[1]) == 60

    @pytest.mark.parametrize("zone", ["+0000", "Mars/Olympus", ""])
    def test_a_zone_it_cannot_resolve_is_refused_not_guessed(self, zone):
        from services.interview_auto_booking import BookingValidationError

        with pytest.raises(BookingValidationError):
            normalized_schedule({"interview": {
                "date": "2026-09-11", "time": "08:30 AM",
                "end_time": "09:30 AM", "timezone": zone,
            }})


class TestBookingStillRequiresPersistence:
    """Routing is not booking: everything downstream still has to pass."""

    def test_routing_only_decides_whether_to_analyse(self):
        decision = agent.routing_decision(SUBJECT, BODY, "", SENDER)
        assert set(decision) >= {"send_to_ai", "reason"}
        assert "booking_status" not in decision
        assert "booked" not in decision

    def test_the_persisted_statuses_are_still_re_read(self):
        import inspect

        from services import interview_auto_booking as ab

        source = inspect.getsource(ab._execute_auto_booking)
        success_audit = source.index("auto_booked=True")
        assert source.index("_confirm_slot_still_stored") < success_audit
