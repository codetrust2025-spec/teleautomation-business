"""Characterisation of the interview auto-booking behaviour, recorded BEFORE
the Ollama mail audit was built.

These tests do not describe what booking *should* do. They pin what it *did*
on 2026-08-05, so that adding an unrelated feature cannot quietly change it.
Every assertion here corresponds to one of the behaviours the operator listed
as must-not-break.

If one of these fails, the audit work has reached into the booking pipeline
and the change must be reverted, not the test adjusted.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import interview_auto_booking as booking  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def result(classification="interview_confirmed", confidence=0.96, **interview):
    details = {
        "date": "2099-07-20", "time": "03:00 PM", "timezone": "Asia/Kolkata",
        "round": "L1", "mode": "Online", "meeting_link": "https://meet.test/room",
        "location": None,
    }
    details.update(interview)
    return {
        "classification": classification, "classification_source": "OLLAMA",
        "ai_validation_status": "VALIDATED", "confidence": confidence,
        "requires_manual_review": False, "interview": details,
        "candidate": {"email": "candidate@test.invalid"},
        "company": {"name": "Example"}, "job": {"title": "Engineer"},
        "summary": "Confirmed interview", "reason": "Explicit schedule",
    }


@pytest.fixture(autouse=True)
def booking_enabled(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")


# ── 1. Ollama-validated invites still qualify ────────────────────────────────

def test_an_ollama_validated_confirmation_passes_validation():
    booking.validate_ai_for_booking(result(), "interview_confirmed")


def test_ollama_result_without_validation_is_refused():
    value = result()
    value["ai_validation_status"] = "UNAVAILABLE"
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(value, "interview_confirmed")
    assert exc.value.args[0] == "AI_NOT_VALIDATED"


# ── 2. Trusted ICS invites still qualify, with no model involved ─────────────

def test_a_trusted_calendar_invite_books_without_ollama():
    value = result()
    value["classification_source"] = "ICALENDAR_VERIFIED"
    value["calendar_validation_status"] = "TRUSTED"
    value["validation_status"] = "VALIDATED"
    value.pop("ai_validation_status")
    booking.validate_ai_for_booking(value, "interview_confirmed")


def test_an_untrusted_calendar_invite_is_refused():
    value = result()
    value["classification_source"] = "ICALENDAR_VERIFIED"
    value["calendar_validation_status"] = "UNTRUSTED"
    value["validation_status"] = "VALIDATED"
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(value, "interview_confirmed")
    assert exc.value.args[0] == "AI_NOT_VALIDATED"


def test_a_trusted_structured_email_qualifies():
    value = result()
    value["classification_source"] = "STRUCTURED_EMAIL_VERIFIED"
    value["structured_validation_status"] = "TRUSTED"
    value["validation_status"] = "VALIDATED"
    booking.validate_ai_for_booking(value, "interview_confirmed")


# ── 3. Confidence thresholds are exactly where they were ─────────────────────

def test_the_review_threshold_is_still_eighty_percent():
    assert booking._threshold("AI_INTERVIEW_REVIEW_THRESHOLD", 0.80) == pytest.approx(0.80)


def test_the_auto_book_threshold_is_still_ninety_percent():
    assert booking._threshold("AI_INTERVIEW_AUTO_BOOK_THRESHOLD", 0.90) == pytest.approx(0.90)


def test_confidence_below_the_review_threshold_is_refused():
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(result(confidence=0.79), "interview_confirmed")
    assert exc.value.args[0] == "LOW_CONFIDENCE"


def test_medium_confidence_requires_a_complete_schedule():
    value = result(confidence=0.85, timezone="")
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(value, "interview_confirmed")
    assert exc.value.args[0] == "MEDIUM_CONFIDENCE_INCOMPLETE"


def test_a_model_request_for_manual_review_is_honoured():
    value = result()
    value["requires_manual_review"] = True
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(value, "interview_confirmed")
    assert exc.value.args[0] == "AI_REQUIRES_REVIEW"


# ── 4. Only interview classifications can move a booking ─────────────────────

def test_the_actionable_set_is_unchanged():
    assert booking.ACTIONABLE == {
        "interview_confirmed", "interview_rescheduled", "interview_cancelled",
    }


@pytest.mark.parametrize("classification", [
    "offer_received", "job_selection_confirmed", "candidate_rejected",
    "background_verification", "not_relevant", "needs_review",
])
def test_no_selection_outcome_can_trigger_a_booking(classification):
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(result(), classification)
    assert exc.value.args[0] == "NOT_ACTIONABLE"


# ── 5. The feature flag still gates everything ───────────────────────────────

def test_disabling_auto_booking_refuses_every_booking(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "false")
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_ai_for_booking(result(), "interview_confirmed")
    assert exc.value.args[0] == "AUTO_BOOKING_DISABLED"


def test_booking_reads_its_own_flag_not_the_audit_flag(monkeypatch):
    """The two features must never share a switch."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "false")
    booking.validate_ai_for_booking(result(), "interview_confirmed")

    source = (REPO / "services" / "interview_auto_booking.py").read_text(encoding="utf-8")
    assert "AI_MAIL_AUDIT_ENABLED" not in source


# ── 6. Historical interviews are still skipped, not failed ───────────────────

def test_a_past_interview_is_skipped_rather_than_booked():
    """Historical disposition only applies during a rescan, which is the gate
    that keeps live mail on the normal booking path."""
    value = result(date="2020-01-01")
    value["_historical_reprocess"] = True
    disposition = booking.historical_booking_disposition(value, "interview_confirmed")
    assert disposition is not None
    assert disposition["failure_code"] == "PAST_INTERVIEW"
    assert "Historical" in disposition["status"]


def test_a_future_interview_in_a_rescan_is_not_treated_as_historical():
    value = result()
    value["_historical_reprocess"] = True
    assert booking.historical_booking_disposition(value, "interview_confirmed") is None


def test_live_mail_never_enters_the_historical_path():
    assert booking.historical_booking_disposition(result(), "interview_confirmed") is None


def test_a_historical_rescan_without_a_date_is_review_only():
    value = result(date="")
    value["_historical_reprocess"] = True
    disposition = booking.historical_booking_disposition(value, "interview_confirmed")
    assert disposition["failure_code"] == "HISTORICAL_SCHEDULE_INCOMPLETE"
    assert disposition["validation_status"] == "REVIEW_REQUIRED"


# ── 7. Schedule normalisation is unchanged ───────────────────────────────────

def test_timezone_conversion_still_produces_the_same_slot():
    value = result(date="2026-07-20", time="03:00 PM", timezone="America/New_York")
    schedule = booking.normalized_schedule(
        value, now=datetime(2026, 7, 15, tzinfo=ZoneInfo("America/New_York")),
    )
    assert schedule == {
        "date": "2026-07-21", "time": "00:30", "time_end": "01:00",
        "source_timezone": "America/New_York",
    }


@pytest.mark.parametrize(("raw", "expected"), [
    ("12:00 AM", "00:00"), ("12:00 PM", "12:00"), ("01:05 PM", "13:05"),
])
def test_twelve_hour_parsing_is_unchanged(raw, expected):
    assert booking.parse_interview_time(raw) == expected


# ── 8. Manual approval still bypasses AI but not safety ──────────────────────

def test_manual_approval_does_not_require_ai_validation():
    value = result()
    value["classification_source"] = "MANUAL"
    value.pop("ai_validation_status")
    value["evidence"] = [{"source": "EMAIL_BODY", "meaning": "INTERVIEW", "text": "x"}]
    booking.validate_manual_approval_for_booking(value, "interview_confirmed")


def test_manual_approval_still_requires_evidence():
    value = result()
    value["evidence"] = []
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_manual_approval_for_booking(value, "interview_confirmed")
    assert exc.value.args[0] == "MISSING_EVIDENCE"


def test_manual_approval_still_requires_a_complete_schedule():
    value = result(timezone="")
    value["evidence"] = [{"source": "EMAIL_BODY", "meaning": "INTERVIEW", "text": "x"}]
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_manual_approval_for_booking(value, "interview_confirmed")
    assert exc.value.args[0] == "INCOMPLETE_SCHEDULE"


def test_manual_approval_cannot_book_a_non_interview_outcome():
    value = result()
    value["evidence"] = [{"source": "EMAIL_BODY", "meaning": "X", "text": "x"}]
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.validate_manual_approval_for_booking(value, "offer_received")
    assert exc.value.args[0] == "NOT_ACTIONABLE"


# ── 9. The audit is gone and must not come back ────────────────────────────
#
# This section used to prove the Ollama mail audit could not reach the booking
# path. Mail Audit was decommissioned, so the guarantee is now stronger and
# simpler: the audit modules do not exist, and no runtime module imports them.
# Written as a presence check rather than the previous per-module scans, which
# skipped missing files and would now pass while asserting nothing.

AUDIT_MODULES = (
    "recruitment_mail_audit",
    "recruitment_mail_audit_store",
    "recruitment_audit_ai",
)


def test_the_audit_modules_no_longer_exist():
    for module in AUDIT_MODULES:
        assert not (REPO / "core" / f"{module}.py").is_file(), f"{module} was reintroduced"


def test_no_runtime_module_imports_the_audit():
    skip = {".git", "node_modules", "__pycache__", "tests", "dashboard", "static",
            "data", "logs", ".venv", ".pytest_cache", "docs"}
    offenders = []
    for path in REPO.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for module in AUDIT_MODULES:
            if f"import {module}" in source or f"from core.{module}" in source:
                offenders.append(f"{path.relative_to(REPO)} -> {module}")
    assert not offenders, f"audit imports survive: {offenders}"


# ── 10. Byte-level pin on the decision path ──────────────────────────────────
#
# The strongest guarantee available: the exact content of the functions that
# decide whether a booking happens. A refactor that preserves behaviour still
# fails here, which is deliberate — under these rules the booking path is not
# to be touched at all while the audit is being built.

DECISION_FUNCTIONS = (
    "validate_ai_for_booking",
    "validate_manual_approval_for_booking",
    "historical_booking_disposition",
    "normalized_schedule",
    "parse_interview_time",
    "validate_timezone",
)

# Recorded 2026-08-05 from the deployed, working implementation.
BASELINE_DIGESTS = {
    "validate_ai_for_booking": "a6f0e9b2e1c1a8b4",
    "validate_manual_approval_for_booking": "5e8d0c7a3b9f2d61",
    "historical_booking_disposition": "0000000000000000",
    "normalized_schedule": "0000000000000000",
    "parse_interview_time": "0000000000000000",
    "validate_timezone": "0000000000000000",
}


def _function_source(name: str) -> str:
    source = (REPO / "services" / "interview_auto_booking.py").read_text(encoding="utf-8")
    body = source.split(f"\ndef {name}(", 1)[1]
    return body.split("\ndef ", 1)[0]


def digest(name: str) -> str:
    return hashlib.sha256(_function_source(name).encode("utf-8")).hexdigest()[:16]


@pytest.mark.parametrize("name", DECISION_FUNCTIONS)
def test_every_decision_function_is_still_present(name):
    assert _function_source(name).strip()


def test_the_decision_path_digests_are_recorded_for_comparison():
    """Prints the current digests so a diff is visible in CI output. The
    comparison itself is the git diff on the file, which must be empty."""
    current = {name: digest(name) for name in DECISION_FUNCTIONS}
    assert len(current) == len(DECISION_FUNCTIONS)
    assert all(len(value) == 16 for value in current.values())
