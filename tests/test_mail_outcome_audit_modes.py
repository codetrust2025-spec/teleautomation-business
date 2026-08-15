"""The selection audit and the interview-slot audit must never share a total.

Mixing them is what made the original report unreadable: a mailbox full of
interview invitations counted as hiring progress. These tests pin the partition
itself, so a category added later has to be assigned to exactly one mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_mail_audit as engine  # noqa: E402
from core import recruitment_mail_audit_store as audit  # noqa: E402


# ── The partition ────────────────────────────────────────────────────────────

def test_the_two_modes_share_no_outcome():
    assert engine.SELECTION_OUTCOMES & engine.INTERVIEW_OUTCOMES == frozenset()


def test_every_mail_outcome_belongs_to_exactly_one_mode_or_none():
    for outcome in engine.OUTCOMES:
        modes = [
            outcome in engine.SELECTION_OUTCOMES,
            outcome in engine.INTERVIEW_OUTCOMES,
        ]
        # NOT_RELEVANT is the absence of an outcome and belongs to neither.
        assert sum(modes) <= 1, f"{outcome} claimed by both modes"
        if outcome != engine.NOT_RELEVANT:
            assert sum(modes) == 1, f"{outcome} belongs to no mode"


def test_selection_mode_contains_every_category_the_operator_asked_for():
    for outcome in (
        engine.VERIFIED_OFFER_LETTER, engine.FINAL_SELECTION, engine.OFFER_INDICATION,
        engine.JOINING_CONFIRMED, engine.BACKGROUND_VERIFICATION, engine.SHORTLISTED,
        engine.REJECTED, engine.MANUAL_REVIEW_REQUIRED,
    ):
        assert outcome in engine.SELECTION_OUTCOMES


def test_selection_mode_excludes_every_interview_category():
    for outcome in (
        engine.INTERVIEW_INVITE, engine.INTERVIEW_RESCHEDULED, engine.INTERVIEW_CANCELLED,
    ):
        assert outcome not in engine.SELECTION_OUTCOMES
    for outcome in engine.BOOKING_OUTCOMES:
        assert engine.mode_for_outcome(outcome) == engine.MODE_INTERVIEW


def test_interview_mode_excludes_every_selection_category():
    for outcome in (
        engine.VERIFIED_OFFER_LETTER, engine.FINAL_SELECTION, engine.OFFER_INDICATION,
        engine.JOINING_CONFIRMED, engine.SHORTLISTED, engine.REJECTED,
        engine.BACKGROUND_VERIFICATION,
    ):
        assert outcome not in engine.INTERVIEW_OUTCOMES
        assert engine.mode_for_outcome(outcome) == engine.MODE_SELECTION


def test_next_round_is_selection_progress_not_slot_mechanics():
    """A cleared round says the company advanced the candidate, like a
    shortlist. It is not a statement about a booked slot."""
    assert engine.mode_for_outcome(engine.NEXT_ROUND) == engine.MODE_SELECTION
    assert engine.mode_for_outcome(engine.SHORTLISTED) == engine.MODE_SELECTION


def test_mode_defaults_to_selection():
    assert engine.normalize_mode(None) == engine.MODE_SELECTION
    assert engine.normalize_mode("") == engine.MODE_SELECTION
    assert engine.normalize_mode("nonsense") == engine.MODE_SELECTION
    assert engine.normalize_mode("interview") == engine.MODE_INTERVIEW
    assert engine.normalize_mode("INTERVIEW") == engine.MODE_INTERVIEW


def test_outcomes_for_mode_returns_only_that_mode():
    assert engine.outcomes_for_mode(engine.MODE_SELECTION) == engine.SELECTION_OUTCOMES
    assert engine.outcomes_for_mode(engine.MODE_INTERVIEW) == engine.INTERVIEW_OUTCOMES


def test_tile_lists_do_not_overlap():
    selection = {category for _key, category in audit.SELECTION_TILES}
    interview = {category for _key, category in audit.INTERVIEW_TILES}
    assert selection & interview == set()


def test_tile_keys_are_unique_across_modes():
    """A shared key would let one mode's number render under the other."""
    selection = {key for key, _ in audit.SELECTION_TILES}
    interview = {key for key, _ in audit.INTERVIEW_TILES}
    assert selection & interview == set()


# ── Booking outcome mapping ──────────────────────────────────────────────────
#
# Rows below use the exact value combinations present in Production.

def booking(**overrides):
    base = {
        "auto_booked": False, "booking_status": "", "validation_status": "",
        "duplicate_check_status": "NOT_CHECKED", "conflict_check_status": "NOT_CHECKED",
        "failure_code": None,
    }
    base.update(overrides)
    return base


def test_auto_booked_row_is_a_booking():
    assert engine.booking_outcome(
        booking(auto_booked=True, booking_status="Auto Booked", validation_status="PASSED")
    ) == engine.BOOKING_AUTO_BOOKED


def test_manually_approved_booking_is_a_booking():
    assert engine.booking_outcome(
        booking(auto_booked=True, booking_status="Approved & Booked",
                validation_status="MANUAL_APPROVED")
    ) == engine.BOOKING_AUTO_BOOKED


def test_duplicate_is_reported_as_duplicate_not_blocked():
    assert engine.booking_outcome(
        booking(booking_status="Duplicate Ignored", duplicate_check_status="DUPLICATE",
                failure_code="DUPLICATE_BOOKING")
    ) == engine.BOOKING_DUPLICATE_IGNORED


def test_slot_conflict_is_its_own_category():
    assert engine.booking_outcome(
        booking(booking_status="Blocked", conflict_check_status="CONFLICT",
                failure_code="SLOT_CONFLICT")
    ) == engine.BOOKING_SLOT_CONFLICT


@pytest.mark.parametrize("code", ["INCOMPLETE_SCHEDULE", "HISTORICAL_SCHEDULE_INCOMPLETE"])
def test_incomplete_schedule_is_missing_date_or_time(code):
    assert engine.booking_outcome(
        booking(booking_status="Blocked", failure_code=code)
    ) == engine.BOOKING_MISSING_SCHEDULE


def test_past_interview_is_not_reported_as_a_blocked_booking():
    """The pipeline behaved correctly; calling it blocked invents a failure."""
    assert engine.booking_outcome(
        booking(booking_status="Historical Skipped", failure_code="PAST_INTERVIEW",
                validation_status="SKIPPED")
    ) == engine.BOOKING_HISTORICAL_SKIPPED


def test_validation_failure_is_a_blocked_booking():
    assert engine.booking_outcome(
        booking(booking_status="Blocked", validation_status="BLOCKED",
                failure_code="AI_NOT_VALIDATED")
    ) == engine.BOOKING_BLOCKED


def test_review_required_is_a_blocked_booking():
    assert engine.booking_outcome(
        booking(booking_status="Review Required", validation_status="REVIEW_REQUIRED")
    ) == engine.BOOKING_BLOCKED


def test_every_booking_outcome_is_an_interview_mode_category():
    for category in engine.BOOKING_OUTCOMES:
        assert category in engine.INTERVIEW_MODE_CATEGORIES


# ── Mode-scoped rollups ──────────────────────────────────────────────────────

def finding(outcome, **overrides):
    base = {
        "id": f"finding-{outcome}", "canonical_candidate_id": "cand-1", "outcome": outcome,
        "confidence": 85.0, "received_at": "2026-08-01", "company_name": "Acme",
        "company_domain": "acme.example", "sender_domain": "acme.example",
        "authenticity": engine.AUTHENTICITY_PASS, "manual_review_required": False,
        "pipeline_outcome": None, "pipeline_agreement": "NO_PIPELINE_RESULT",
        "subject": "s",
    }
    base.update(overrides)
    return base


def base_row():
    return {
        "canonical_candidate_id": "cand-1", "candidate_id": "cand-1",
        "candidate_name": "Test Candidate", "email_address": "c@example.com",
        "mailbox_id": "mailbox-1", "monitoring_status": "MONITORING_ACTIVE",
        "connection_status": "CONNECTED", "scan_status": "SCANNED",
        "messages_examined": 10, "system_status": None,
        "last_successful_sync_at": None,
    }


def wire(monkeypatch, findings_by_mode, bookings=None, gaps=None):
    monkeypatch.setattr(audit, "_base_candidate_rows", lambda: [base_row()])
    monkeypatch.setattr(
        audit, "_findings_for_mode",
        lambda mode: {"cand-1": findings_by_mode.get(engine.normalize_mode(mode), [])},
    )
    monkeypatch.setattr(
        audit, "_booking_rows_by_candidate",
        lambda: {"cand-1": bookings or []},
    )
    monkeypatch.setattr(audit, "_gap_counts_by_candidate", lambda mode: gaps or {})


def test_selection_mode_never_counts_interview_results(monkeypatch):
    wire(monkeypatch, {
        engine.MODE_SELECTION: [finding(engine.SHORTLISTED)],
        engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)],
    })
    rows = audit.mode_candidate_rows(engine.MODE_SELECTION)
    counts = rows[0]["outcome_counts"]
    assert counts == {engine.SHORTLISTED: 1}
    assert engine.INTERVIEW_INVITE not in counts
    assert rows[0]["strongest_outcome"] == engine.SHORTLISTED


def test_interview_mode_never_counts_selection_results(monkeypatch):
    wire(monkeypatch, {
        engine.MODE_SELECTION: [finding(engine.VERIFIED_OFFER_LETTER, confidence=92.0)],
        engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)],
    })
    rows = audit.mode_candidate_rows(engine.MODE_INTERVIEW)
    counts = rows[0]["outcome_counts"]
    assert engine.VERIFIED_OFFER_LETTER not in counts
    assert counts[engine.INTERVIEW_INVITE] == 1
    assert rows[0]["strongest_outcome"] == engine.INTERVIEW_INVITE


def test_interview_mode_adds_booking_outcomes(monkeypatch):
    wire(
        monkeypatch,
        {engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)]},
        bookings=[
            {**booking(auto_booked=True, booking_status="Auto Booked"),
             "booking_outcome": engine.BOOKING_AUTO_BOOKED},
            {**booking(booking_status="Blocked", failure_code="AI_NOT_VALIDATED"),
             "booking_outcome": engine.BOOKING_BLOCKED},
        ],
    )
    rows = audit.mode_candidate_rows(engine.MODE_INTERVIEW)
    counts = rows[0]["outcome_counts"]
    assert counts[engine.BOOKING_AUTO_BOOKED] == 1
    assert counts[engine.BOOKING_BLOCKED] == 1
    # A completed booking is the strongest statement about a slot.
    assert rows[0]["strongest_outcome"] == engine.BOOKING_AUTO_BOOKED


def test_selection_mode_ignores_booking_rows_entirely(monkeypatch):
    wire(
        monkeypatch,
        {engine.MODE_SELECTION: [finding(engine.REJECTED)]},
        bookings=[{**booking(auto_booked=True), "booking_outcome": engine.BOOKING_AUTO_BOOKED}],
    )
    rows = audit.mode_candidate_rows(engine.MODE_SELECTION)
    assert engine.BOOKING_AUTO_BOOKED not in rows[0]["outcome_counts"]
    assert rows[0]["strongest_outcome"] == engine.REJECTED


def test_status_mismatch_is_only_claimed_by_the_selection_audit(monkeypatch):
    """Interview-slot results say nothing about whether a candidate was hired."""
    wire(monkeypatch, {
        engine.MODE_SELECTION: [finding(engine.VERIFIED_OFFER_LETTER, confidence=92.0)],
        engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)],
    })
    selection = audit.mode_candidate_rows(engine.MODE_SELECTION)[0]
    interview = audit.mode_candidate_rows(engine.MODE_INTERVIEW)[0]
    assert selection["status_mismatch"] is True
    assert interview["status_mismatch"] is False
    assert interview["mismatch_detail"] is None


def test_candidate_with_no_selection_evidence_reports_none(monkeypatch):
    wire(monkeypatch, {
        engine.MODE_SELECTION: [],
        engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)],
    })
    row = audit.mode_candidate_rows(engine.MODE_SELECTION)[0]
    assert row["strongest_outcome"] == engine.NOT_RELEVANT
    assert row["relevant_messages"] == 0
    assert "No selection evidence" in row["recommended_action"]


def test_interview_recommendation_prioritises_the_actionable_failure():
    assert "slot conflict" in audit._interview_recommendation(
        {engine.BOOKING_SLOT_CONFLICT: 1, engine.BOOKING_AUTO_BOOKED: 1}).lower()
    assert "blocked" in audit._interview_recommendation(
        {engine.BOOKING_BLOCKED: 1, engine.BOOKING_AUTO_BOOKED: 2}).lower()
    assert "booked automatically" in audit._interview_recommendation(
        {engine.BOOKING_AUTO_BOOKED: 1}).lower()


# ── Export ───────────────────────────────────────────────────────────────────

def test_export_carries_only_the_selected_mode(monkeypatch):
    wire(monkeypatch, {
        engine.MODE_SELECTION: [finding(engine.SHORTLISTED)],
        engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)],
    })
    selection_csv = audit.export_csv({"mode": engine.MODE_SELECTION})
    header = selection_csv.splitlines()[0].lower()
    assert "shortlisted" in header
    assert "interview_invite" not in header
    assert engine.MODE_SELECTION in selection_csv

    interview_csv = audit.export_csv({"mode": engine.MODE_INTERVIEW})
    header = interview_csv.splitlines()[0].lower()
    assert "interview_invite" in header
    assert "verified_offer_letter" not in header


def test_selection_export_carries_the_status_columns(monkeypatch):
    wire(monkeypatch, {engine.MODE_SELECTION: [finding(engine.FINAL_SELECTION)]})
    header = audit.export_csv({"mode": engine.MODE_SELECTION}).splitlines()[0]
    assert "system_status" in header and "status_mismatch" in header


def test_interview_export_omits_hiring_status_columns(monkeypatch):
    wire(monkeypatch, {engine.MODE_INTERVIEW: [finding(engine.INTERVIEW_INVITE)]})
    header = audit.export_csv({"mode": engine.MODE_INTERVIEW}).splitlines()[0]
    assert "status_mismatch" not in header


def test_export_has_one_row_per_candidate(monkeypatch):
    wire(monkeypatch, {engine.MODE_SELECTION: [finding(engine.SHORTLISTED)]})
    lines = [line for line in audit.export_csv({"mode": engine.MODE_SELECTION}).splitlines() if line]
    assert len(lines) == 2  # header plus the one candidate


# ── Read-only guarantee survives the split ───────────────────────────────────

def test_mode_reporting_never_writes_candidate_status():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    for name in ("mode_candidate_rows", "system_summary", "export_csv", "gap_totals"):
        body = source.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
        assert "candidate_job_status" not in body, f"{name} touches candidate status"
        assert "_apply_status" not in body, f"{name} applies a status"
