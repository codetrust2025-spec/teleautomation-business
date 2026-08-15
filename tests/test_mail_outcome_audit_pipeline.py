"""Pipeline-gap and rollup contract tests for the mail outcome audit.

These cover the audit's second job: not "what did the mail say" but "did
TeleAutomation handle it". Everything runs against a fake database cursor, so
no test can reach a real mailbox or a real candidate record.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_mail_audit as engine  # noqa: E402
from core import recruitment_mail_audit_store as audit  # noqa: E402


UTC = timezone.utc
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class FakeCursor:
    """Returns one canned result set; records the SQL it was asked to run."""

    def __init__(self, rows, columns):
        self._rows = rows
        self.description = [type("C", (), {"name": name})() for name in columns]
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_db(monkeypatch, rows=(), columns=("status", "total", "oldest")):
    cursor = FakeCursor(list(rows), list(columns))

    @contextmanager
    def connection():
        yield FakeConnection(cursor)

    monkeypatch.setattr(audit, "get_connection", connection)
    return cursor


def mailbox(**overrides):
    base = {
        "id": "mailbox-1",
        "candidate_id": "candidate-1",
        "canonical_candidate_id": "candidate-1",
        "email_address": "candidate@gmail.com",
        "connection_status": "CONNECTED",
        "monitoring_enabled": True,
        "last_successful_sync_at": NOW - timedelta(minutes=5),
        "created_at": NOW - timedelta(days=30),
        "last_error_code": None,
        "last_error_message": None,
    }
    base.update(overrides)
    return base


def finding(**overrides):
    base = {
        "provider_message_id": "gmail-1",
        "mailbox_message_id": "message-1",
        "outcome": engine.FINAL_SELECTION,
        "confidence": 85.0,
        "received_at": NOW - timedelta(days=1),
        "company_name": "Acme Corp",
        "company_domain": "acme-corp.example",
        "sender_domain": "acme-corp.example",
        "authenticity": engine.AUTHENTICITY_PASS,
        "manual_review_required": False,
        "pipeline_agreement": "AGREE",
        "pipeline_outcome": engine.FINAL_SELECTION,
        "content_signature": "signature-1",
        "attachment_evidence": [],
        "_processing_status": "COMPLETED",
        "_ignore_reason": None,
        "_ai_error": None,
        "_message_candidate_id": "candidate-1",
    }
    base.update(overrides)
    return base


def gap_types(gaps):
    return {item["gap_type"] for item in gaps}


# ── 11. AI queue failure ─────────────────────────────────────────────────────

def test_message_stuck_in_the_ai_queue_is_reported(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(_processing_status="AI_RETRY_PENDING", _ai_error="AIGatewayError"),
    ])
    assert audit.GAP_AI_QUEUE_FAILURE in gap_types(gaps)
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_AI_QUEUE_FAILURE)
    assert entry["severity"] == "HIGH"
    assert "AIGatewayError" in entry["detail"]


def test_schema_validation_failure_is_its_own_gap(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [finding(_processing_status="VALIDATION_FAILED")])
    assert audit.GAP_SCHEMA_VALIDATION_FAILED in gap_types(gaps)


def test_processing_exception_is_reported(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [finding(_processing_status="FAILED")])
    assert audit.GAP_PROCESSING_EXCEPTION in gap_types(gaps)


# ── Sync failures ────────────────────────────────────────────────────────────

def test_mailbox_in_error_is_reported_as_a_sync_failure(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(
        mailbox(connection_status="ERROR", last_error_code="HTTPError",
                last_error_message="invalid_grant"), [],
    )
    assert audit.GAP_SYNC_FAILURE in gap_types(gaps)
    assert any("invalid_grant" in item["detail"] for item in gaps)


def test_never_synced_mailbox_is_reported(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(last_successful_sync_at=None), [])
    assert audit.GAP_SYNC_FAILURE in gap_types(gaps)


def test_undelivered_gmail_ids_are_reported_as_incomplete_sync(monkeypatch):
    fake_db(monkeypatch, rows=[("DEAD_LETTER", 4, NOW - timedelta(days=2))])
    gaps = audit._mailbox_gaps(mailbox(), [])
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_SYNC_INCOMPLETE)
    assert entry["severity"] == "HIGH"
    assert entry["metadata"]["count"] == 4


# ── 8. Duplicate email / thread ──────────────────────────────────────────────

def test_identical_content_across_two_messages_is_counted_once(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(provider_message_id="gmail-1", content_signature="same"),
        finding(provider_message_id="gmail-2", mailbox_message_id="message-2",
                content_signature="same"),
    ])
    duplicates = [item for item in gaps if item["gap_type"] == audit.GAP_DEDUP_SUPPRESSED]
    assert len(duplicates) == 1
    assert duplicates[0]["metadata"]["duplicate_of"] == "gmail-1"


def test_pipeline_suppressing_a_real_outcome_as_duplicate_is_a_gap(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(_processing_status="DUPLICATE", _ignore_reason="body-only hash",
                content_signature="unique-1"),
    ])
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_DEDUP_SUPPRESSED)
    assert entry["severity"] == "MEDIUM"
    assert "body-only hash" in entry["detail"]


# ── Missed and misclassified ─────────────────────────────────────────────────

def test_confident_outcome_without_a_pipeline_event_is_a_missed_event(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(pipeline_agreement="NO_PIPELINE_RESULT", pipeline_outcome=None),
    ])
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_MISSING_EVENT)
    assert entry["severity"] == "HIGH"


def test_pipeline_claiming_more_than_the_evidence_is_high_severity(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(outcome=engine.INTERVIEW_INVITE, pipeline_agreement="PIPELINE_STRONGER",
                pipeline_outcome=engine.VERIFIED_OFFER_LETTER),
    ])
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_MISCLASSIFIED)
    assert entry["severity"] == "HIGH"
    assert "overstate" in entry["detail"]


def test_low_confidence_evidence_is_reported_not_promoted(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(confidence=40.0, pipeline_agreement="NO_PIPELINE_RESULT", pipeline_outcome=None),
    ])
    assert audit.GAP_LOW_CONFIDENCE in gap_types(gaps)
    assert audit.GAP_MISSING_EVENT not in gap_types(gaps)


# ── 10. Attachment parsing failure ───────────────────────────────────────────

def test_unreadable_attachment_is_reported_as_a_gap(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [
        finding(attachment_evidence=[
            {"filename": "offer.pdf", "extraction_status": "FAILED"},
        ]),
    ])
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_ATTACHMENT_EXTRACTION_FAILED)
    assert entry["metadata"]["filename"] == "offer.pdf"


# ── 13. Incorrect mailbox mapping ────────────────────────────────────────────

def test_message_stored_against_a_different_candidate_is_flagged(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(candidate_id="candidate-1"), [
        finding(_message_candidate_id="candidate-999"),
    ])
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_CANDIDATE_MAPPING)
    assert entry["severity"] == "HIGH"
    assert entry["metadata"] == {"message_candidate_id": "candidate-999",
                                 "mailbox_candidate_id": "candidate-1"}


def test_correctly_mapped_message_raises_no_mapping_gap(monkeypatch):
    fake_db(monkeypatch)
    gaps = audit._mailbox_gaps(mailbox(), [finding()])
    assert audit.GAP_CANDIDATE_MAPPING not in gap_types(gaps)


# ── 14. Historical rescan limitation ─────────────────────────────────────────

def test_mail_older_than_the_first_stored_message_is_reported_as_uncovered(monkeypatch):
    fake_db(monkeypatch)
    connected = NOW - timedelta(days=60)
    gaps = audit._mailbox_gaps(
        mailbox(created_at=connected),
        [finding(received_at=connected + timedelta(days=20))],
    )
    entry = next(item for item in gaps if item["gap_type"] == audit.GAP_HISTORICAL_LIMIT)
    assert "historical rescan" in entry["detail"].lower()


def test_full_history_coverage_raises_no_historical_gap(monkeypatch):
    fake_db(monkeypatch)
    connected = NOW - timedelta(days=60)
    gaps = audit._mailbox_gaps(
        mailbox(created_at=connected),
        [finding(received_at=connected + timedelta(hours=2))],
    )
    assert audit.GAP_HISTORICAL_LIMIT not in gap_types(gaps)


# ── 12. Two candidates with similar names ────────────────────────────────────

def test_similar_names_stay_separate_because_rollups_key_on_candidate_id():
    """Names are display data. The rollup identity is the immutable id."""
    shared = [finding()]
    first = audit.build_candidate_rollup(
        mailbox(id="mailbox-a", candidate_id="9000031215",
                canonical_candidate_id="9000031215", email_address="anil.k@gmail.com"),
        shared, system_status=None, system_source=None, candidate_name="Anil Kumar",
    )
    second = audit.build_candidate_rollup(
        mailbox(id="mailbox-b", candidate_id="9866445691",
                canonical_candidate_id="9866445691", email_address="anilkumar.m@gmail.com"),
        [], system_status=None, system_source=None, candidate_name="Anil Kumar M",
    )
    assert first["canonical_candidate_id"] != second["canonical_candidate_id"]
    assert first["strongest_outcome"] == engine.FINAL_SELECTION
    assert second["strongest_outcome"] == engine.NOT_RELEVANT
    assert second["relevant_messages"] == 0


# ── 17. Multiple companies, and the rollup contract ──────────────────────────

def test_rollup_lists_every_company_and_the_strongest_outcome():
    row = audit.build_candidate_rollup(
        mailbox(),
        [
            finding(provider_message_id="g1", outcome=engine.INTERVIEW_INVITE,
                    company_name="Acme Corp", company_domain="acme-corp.example"),
            finding(provider_message_id="g2", outcome=engine.VERIFIED_OFFER_LETTER,
                    confidence=92.0, company_name="Globex", company_domain="globex.example"),
            finding(provider_message_id="g3", outcome=engine.REJECTED,
                    company_name="Initech", company_domain="initech.example"),
        ],
        system_status=None, system_source=None,
    )
    assert row["companies"] == ["Acme Corp", "Globex", "Initech"]
    assert row["strongest_outcome"] == engine.VERIFIED_OFFER_LETTER
    assert row["relevant_messages"] == 3
    assert row["conflicting_evidence"] is False


def test_rollup_flags_conflicting_outcomes_for_one_company():
    row = audit.build_candidate_rollup(
        mailbox(),
        [
            finding(provider_message_id="g1", outcome=engine.FINAL_SELECTION),
            finding(provider_message_id="g2", outcome=engine.REJECTED),
        ],
        system_status=None, system_source=None,
    )
    assert row["conflicting_evidence"] is True
    assert row["manual_review_required"] is True
    assert "conflicting" in row["recommended_action"].lower()


# ── Status mismatch and the approval boundary ────────────────────────────────

def test_mail_evidence_ahead_of_system_status_is_a_mismatch():
    row = audit.build_candidate_rollup(
        mailbox(), [finding(outcome=engine.VERIFIED_OFFER_LETTER, confidence=92.0)],
        system_status="Interview Confirmed", system_source="ai_mail",
    )
    assert row["status_mismatch"] is True
    assert "Offer Received" in row["mismatch_detail"]
    assert "approve" in row["recommended_action"].lower()


def test_system_status_without_mail_evidence_is_a_mismatch():
    row = audit.build_candidate_rollup(
        mailbox(), [], system_status="Selected", system_source="ai_mail",
    )
    assert row["status_mismatch"] is True
    assert "no supporting mail evidence" in row["mismatch_detail"]


def test_matching_status_is_not_a_mismatch():
    row = audit.build_candidate_rollup(
        mailbox(), [finding(outcome=engine.FINAL_SELECTION)],
        system_status="Selected", system_source="ai_mail",
    )
    assert row["status_mismatch"] is False
    assert row["recommended_action"].startswith("No action")


def test_profile_active_with_no_evidence_is_not_a_mismatch():
    row = audit.build_candidate_rollup(
        mailbox(), [], system_status="Profile Active", system_source="manual",
    )
    assert row["status_mismatch"] is False


def test_suspicious_authenticity_routes_to_human_review():
    row = audit.build_candidate_rollup(
        mailbox(),
        [finding(authenticity=engine.AUTHENTICITY_SUSPICIOUS)],
        system_status="Selected", system_source="ai_mail",
    )
    assert row["suspicious_evidence"] is True
    assert "authenticity" in row["recommended_action"].lower()


# ── Scenario 16: a mailbox with nothing in it ────────────────────────────────

def test_mailbox_with_no_relevant_mail_reports_no_outcome():
    row = audit.build_candidate_rollup(
        mailbox(),
        [finding(outcome=engine.NOT_RELEVANT, confidence=60.0)],
        system_status=None, system_source=None,
    )
    assert row["strongest_outcome"] == engine.NOT_RELEVANT
    assert row["relevant_messages"] == 0
    assert row["recommended_action"] == "No meaningful outcome found in this mailbox."


# ── The approval boundary itself ─────────────────────────────────────────────

def test_only_decided_outcomes_map_to_a_candidate_status():
    """Interview scheduling must never be approvable as a hiring status change
    beyond its own interview state, and audit-only outcomes map to nothing."""
    assert audit._AUDIT_TO_CANDIDATE_STATUS[engine.VERIFIED_OFFER_LETTER] == "Offer Received"
    assert audit._AUDIT_TO_CANDIDATE_STATUS[engine.FINAL_SELECTION] == "Selected"
    assert audit._AUDIT_TO_CANDIDATE_STATUS[engine.JOINING_CONFIRMED] == "Joining Confirmed"
    assert engine.NOT_RELEVANT not in audit._AUDIT_TO_CANDIDATE_STATUS
    assert engine.MANUAL_REVIEW_REQUIRED not in audit._AUDIT_TO_CANDIDATE_STATUS


def test_approval_rejects_an_unknown_decision():
    with pytest.raises(ValueError):
        audit.approve_outcome("finding-1", decision="MAYBE", approved_by="admin")


def test_run_audit_is_always_report_only():
    """The runner has no code path that writes candidate status."""
    source = Path(audit.__file__).read_text(encoding="utf-8")
    body = source.split("def run_audit(", 1)[1].split("\ndef ", 1)[0]
    assert "candidate_job_status" not in body
    assert "REPORT_ONLY" in body
    # _apply_status is the single writer, and only approve_outcome calls it.
    assert source.count("_apply_status(") == 2
    approve = source.split("def approve_outcome(", 1)[1].split("\ndef ", 1)[0]
    assert "_apply_status(" in approve


def test_pipeline_status_mapping_is_total_over_the_agent_statuses():
    """Every status the live agent can emit must compare against the audit."""
    from services import recruitment_mail_agent as agent
    unmapped = [status for status in agent.STATUSES if status not in audit._PIPELINE_TO_AUDIT]
    assert unmapped == []
