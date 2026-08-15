"""A parseable interview time must not be rejected for its formatting.

Production message 5494b05a ("Interview scheduled with Altimetrik on Fri,
August 14, 2:00 PM - 3:00 PM IST"): routing passed at 0.92, Ollama ran, and
validate_result raised "interview requires valid 12-hour time" because the
model's time did not match the exact H:MM AM/PM regex. Identical input,
identical failure, every retry — the interview never surfaced and was never
booked.
"""

import pytest
from services.recruitment_mail_agent import _normalise_interview_time as norm


@pytest.mark.parametrize("raw,expected", [
    ("02:00 PM", "02:00 PM"),
    ("2:00PM", "02:00 PM"),
    ("02:00 PM", "02:00 PM"),
    ("2:00 PM IST", "02:00 PM"),
    ("2:00 PM - 3:00 PM", "02:00 PM"),          # a range starts the interview
    ("2:00 PM - 3:00 PM IST", "02:00 PM"),
    ("2:00 PM \u2013 3:00 PM", "02:00 PM"),      # en dash
    ("11:00AM IST", "11:00 AM"),
    ("3 PM", "03:00 PM"),
    ("2.00 PM", "02:00 PM"),
])
def test_parseable_times_are_normalised(raw, expected):
    assert norm(raw) == expected


@pytest.mark.parametrize("raw", [
    "", None, "sometime tomorrow", "TBD", "afternoon",
    "25:00", "13:00 PM", "0:00 AM", "99:99",
    # 24-hour is rejected by design: an explicit AM/PM is the evidence
    # that the source stated the time unambiguously.
    "14:00", "09:30", "17:00",
])
def test_genuinely_unreadable_times_still_fail(raw):
    """The guard survives — only formatting is forgiven, never absence."""
    assert norm(raw) == ""


def test_validation_writes_the_normalised_time_back():
    import inspect
    from services import recruitment_mail_agent as agent
    src = inspect.getsource(agent.validate_result)
    assert "_normalise_interview_time" in src
    assert 'interview["time"] = normalised_time' in src
    # the raw regex rejection must be gone
    assert '(?:0?[1-9]|1[0-2]):[0-5]' not in src


def test_a_missing_time_still_invalidates_the_interview():
    import inspect
    from services import recruitment_mail_agent as agent
    src = inspect.getsource(agent.validate_result)
    assert "time_valid = False" in src
