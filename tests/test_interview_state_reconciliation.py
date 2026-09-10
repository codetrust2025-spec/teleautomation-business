from __future__ import annotations

from services.interview_state_reconciliation import build_reconciliation_report


def test_report_finds_false_auto_booked_claim_without_mutating_inputs():
    candidates = [{"id": "slot-1", "slot_confirmed": True, "date": "2026-09-11", "time": "14:00", "time_end": "15:00"}]
    audits = [{"id": "audit-1", "candidate_id": "candidate-1", "booking_id": "missing-slot", "email_analysis_id": "analysis-1", "auto_booked": True}]
    notifications = [{"candidate_id": "candidate-1", "booking_id": "missing-slot", "booking_audit_id": "audit-1", "booking_status": "Auto Booked"}]
    analyses = [{"id": "analysis-1"}]

    report = build_reconciliation_report(candidates=candidates, audits=audits, notifications=notifications, analyses=analyses)

    assert report["mode"] == "report_only"
    assert {row["code"] for row in report["findings"]} == {
        "AUTO_BOOKED_WITHOUT_PERSISTED_SLOT", "NOTIFICATION_CLAIMS_BOOKED_WITHOUT_SLOT",
    }
    assert candidates[0]["slot_confirmed"] is True
    assert audits[0]["booking_id"] == "missing-slot"


def test_report_finds_ai_slot_without_audit_and_broken_notification_audit_link():
    report = build_reconciliation_report(
        candidates=[{"id": "slot-1", "slot_confirmed": True, "interview_booking_source": "ai_auto_booked", "date": "2026-09-11", "time": "14:00", "time_end": "15:00"}],
        audits=[],
        notifications=[{"candidate_id": "candidate-1", "booking_id": "slot-1", "booking_audit_id": "missing-audit", "booking_status": "Auto Booked"}],
        analyses=[],
    )

    assert {row["code"] for row in report["findings"]} == {
        "PERSISTED_AI_SLOT_WITHOUT_AUDIT", "NOTIFICATION_AUDIT_MISSING",
    }
