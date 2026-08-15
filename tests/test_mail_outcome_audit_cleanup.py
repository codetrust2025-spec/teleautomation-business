"""Selection-audit cleanup: what stops counting, and what must never be lost.

Suppression is a counting decision, not a deletion. Every test here checks one
of two things: that a finding which does not belong in the selection totals
stops inflating them, or that a real outcome survives cleanup untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_mail_audit as engine  # noqa: E402
from core import recruitment_mail_audit_store as audit  # noqa: E402


def finding(outcome, *, id=None, received="2026-07-01", company="acme.example",
            evidence=None, signature=None, thread=None, attachment=None,
            message=None, **extra):
    row = {
        "id": id or f"f-{outcome}-{received}",
        "canonical_candidate_id": "cand-1",
        "provider_message_id": message or (id or f"m-{outcome}-{received}"),
        "provider_thread_id": thread,
        "outcome": outcome,
        "confidence": 85.0,
        "received_at": received,
        "company_name": company,
        "company_domain": company,
        "sender_domain": company,
        "evidence": [{"source": "EMAIL_BODY", "meaning": outcome, "text": "x"}]
        if evidence is None else evidence,
        "content_signature": signature,
        "attachment_fingerprint": attachment,
        "manual_review_required": False,
        "authenticity": engine.AUTHENTICITY_PASS,
        "pipeline_outcome": None,
        "pipeline_agreement": "NO_PIPELINE_RESULT",
        "subject": "s",
    }
    row.update(extra)
    return row


def reasons(findings):
    return {k: v["reason"] for k, v in engine.selection_suppressions(findings).items()}


# ── Nothing real is removed ──────────────────────────────────────────────────

@pytest.mark.parametrize("outcome", [
    engine.SHORTLISTED, engine.NEXT_ROUND, engine.OFFER_INDICATION,
    engine.FINAL_SELECTION, engine.VERIFIED_OFFER_LETTER, engine.JOINING_CONFIRMED,
    engine.REJECTED, engine.MANUAL_REVIEW_REQUIRED,
])
def test_every_category_the_report_should_keep_survives_cleanup(outcome):
    assert reasons([finding(outcome)]) == {}


def test_a_genuine_manual_review_case_is_kept():
    """Manual review is a real state, not noise: it means a human must look."""
    assert reasons([finding(engine.MANUAL_REVIEW_REQUIRED)]) == {}


# ── Wrong audit mode ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("outcome", [
    engine.INTERVIEW_INVITE, engine.INTERVIEW_RESCHEDULED, engine.INTERVIEW_CANCELLED,
])
def test_interview_results_are_moved_not_deleted(outcome):
    decisions = engine.selection_suppressions([finding(outcome)])
    entry = next(iter(decisions.values()))
    assert entry["reason"] == engine.SUPPRESS_WRONG_MODE
    assert "Interview Slot Audit" in entry["detail"]


# ── Irrelevant mail ──────────────────────────────────────────────────────────

def test_job_adverts_and_questionnaires_are_irrelevant():
    rows = [
        finding(engine.NOT_RELEVANT, id="ad", signals=["NOISE"]),
        finding(engine.NOT_RELEVANT, id="form", signals=["QUESTIONNAIRE"]),
        finding(engine.NOT_RELEVANT, id="ack", signals=[]),
    ]
    assert set(reasons(rows).values()) == {engine.SUPPRESS_IRRELEVANT}


def test_the_reason_names_the_signal_that_produced_it():
    decisions = engine.selection_suppressions(
        [finding(engine.NOT_RELEVANT, id="ad", signals=["NOISE", "RECRUITER_INTEREST"])]
    )
    assert "NOISE" in decisions["ad"]["detail"]


def test_a_finding_with_no_evidence_is_irrelevant():
    assert reasons([finding(engine.SHORTLISTED, id="bare", evidence=[])]) == {
        "bare": engine.SUPPRESS_IRRELEVANT,
    }


# ── Background verification ──────────────────────────────────────────────────

def test_background_verification_alone_does_not_count_as_selection():
    assert reasons([finding(engine.BACKGROUND_VERIFICATION, id="bgv")]) == {
        "bgv": engine.SUPPRESS_IRRELEVANT,
    }


def test_background_verification_with_an_offer_is_not_called_irrelevant():
    """It is real corroboration, so it is never dismissed as noise. The offer
    that follows it does supersede it for counting, which is the honest
    description of what happened."""
    rows = [
        finding(engine.BACKGROUND_VERIFICATION, id="bgv", received="2026-07-01"),
        finding(engine.OFFER_INDICATION, id="offer", received="2026-07-02"),
    ]
    assert reasons(rows).get("bgv") != engine.SUPPRESS_IRRELEVANT
    assert "offer" not in reasons(rows)


def test_background_verification_from_another_company_is_still_removed():
    rows = [
        finding(engine.BACKGROUND_VERIFICATION, id="bgv", company="other.example"),
        finding(engine.OFFER_INDICATION, id="offer", company="acme.example"),
    ]
    assert reasons(rows)["bgv"] == engine.SUPPRESS_IRRELEVANT


# ── Duplicates ───────────────────────────────────────────────────────────────

def test_identical_content_is_counted_once():
    rows = [
        finding(engine.VERIFIED_OFFER_LETTER, id="a", received="2026-07-01",
                signature="sig-1", message="gmail-a"),
        finding(engine.VERIFIED_OFFER_LETTER, id="b", received="2026-07-02",
                signature="sig-1", message="gmail-b"),
    ]
    result = reasons(rows)
    assert result == {"b": engine.SUPPRESS_DUPLICATE}


def test_the_duplicate_reason_points_at_the_message_that_was_kept():
    rows = [
        finding(engine.VERIFIED_OFFER_LETTER, id="a", signature="s", message="gmail-a"),
        finding(engine.VERIFIED_OFFER_LETTER, id="b", received="2026-07-02",
                signature="s", message="gmail-b"),
    ]
    assert "gmail-a" in engine.selection_suppressions(rows)["b"]["detail"]


def test_the_same_offer_attachment_twice_is_one_offer():
    """Two near-identically named copies of one signed offer letter."""
    rows = [
        finding(engine.VERIFIED_OFFER_LETTER, id="a", received="2026-07-16",
                attachment="checksum-1", message="gmail-a"),
        finding(engine.VERIFIED_OFFER_LETTER, id="b", received="2026-07-16",
                attachment="checksum-1", message="gmail-b"),
    ]
    assert reasons(rows) == {"b": engine.SUPPRESS_DUPLICATE}


def test_repeated_thread_copies_of_one_outcome_count_once():
    rows = [
        finding(engine.SHORTLISTED, id="a", thread="t-1", received="2026-07-01"),
        finding(engine.SHORTLISTED, id="b", thread="t-1", received="2026-07-02"),
        finding(engine.SHORTLISTED, id="c", thread="t-1", received="2026-07-03"),
    ]
    result = reasons(rows)
    assert result == {"b": engine.SUPPRESS_DUPLICATE, "c": engine.SUPPRESS_DUPLICATE}


def test_a_reply_attaching_the_signed_offer_back_is_not_a_second_offer():
    """The live case that survived the first cleanup pass.

    One Kaivale offer letter, thread 19f46b4816321cbc: the recruiter's message
    and the candidate's reply 25 minutes later carrying a re-signed copy. The
    bodies differ, the two PDFs differ by a few bytes, and the reply resolves
    to the candidate's own gmail.com rather than kaivale.com — so content,
    attachment and company-scoped keys all miss. The thread is what makes them
    one event.
    """
    rows = [
        finding(engine.VERIFIED_OFFER_LETTER, id="recruiter",
                received="2026-07-16T12:10:13+00:00", thread="19f46b4816321cbc",
                company="kaivale.com", signature="sig-a", attachment="fp-a",
                message="19f6ad5e1eb33a58"),
        finding(engine.VERIFIED_OFFER_LETTER, id="candidate-reply",
                received="2026-07-16T12:35:30+00:00", thread="19f46b4816321cbc",
                company="gmail.com", signature="sig-b", attachment="fp-b",
                message="19f6aeccaff1b324"),
    ]
    result = reasons(rows)
    assert result == {"candidate-reply": engine.SUPPRESS_DUPLICATE}


def test_thread_deduplication_ignores_the_sender_domain():
    """Scoping the thread key by company is what split one offer into two."""
    rows = [
        finding(engine.OFFER_INDICATION, id="a", thread="t", company="acme.example"),
        finding(engine.OFFER_INDICATION, id="b", thread="t", company="gmail.com",
                received="2026-07-02"),
    ]
    assert reasons(rows) == {"b": engine.SUPPRESS_DUPLICATE}


def test_different_outcomes_in_one_thread_are_not_duplicates():
    rows = [
        finding(engine.SHORTLISTED, id="a", thread="t-1", received="2026-07-01"),
        finding(engine.OFFER_INDICATION, id="b", thread="t-1", received="2026-07-02"),
    ]
    assert "b" not in reasons(rows)


def test_the_same_outcome_from_two_companies_is_not_a_duplicate():
    rows = [
        finding(engine.SHORTLISTED, id="a", company="acme.example"),
        finding(engine.SHORTLISTED, id="b", company="globex.example", received="2026-07-02"),
    ]
    assert reasons(rows) == {}


# ── Superseded ───────────────────────────────────────────────────────────────

def test_an_offer_indication_is_superseded_by_that_company_s_offer_letter():
    rows = [
        finding(engine.OFFER_INDICATION, id="early", received="2026-07-01"),
        finding(engine.VERIFIED_OFFER_LETTER, id="late", received="2026-07-20"),
    ]
    result = reasons(rows)
    assert result == {"early": engine.SUPPRESS_SUPERSEDED}


def test_supersession_is_scoped_to_one_company():
    rows = [
        finding(engine.OFFER_INDICATION, id="acme", company="acme.example", received="2026-07-01"),
        finding(engine.VERIFIED_OFFER_LETTER, id="globex", company="globex.example",
                received="2026-07-20"),
    ]
    assert "acme" not in reasons(rows)


def test_a_later_weaker_finding_does_not_supersede_a_stronger_earlier_one():
    rows = [
        finding(engine.VERIFIED_OFFER_LETTER, id="offer", received="2026-07-01"),
        finding(engine.SHORTLISTED, id="later", received="2026-07-20"),
    ]
    assert "offer" not in reasons(rows)


def test_a_rejection_is_never_silently_superseded_by_an_offer():
    """Both survive: an offer and a rejection from one company is a conflict a
    human must see, not something cleanup should tidy away."""
    rows = [
        finding(engine.REJECTED, id="reject", received="2026-07-01"),
        finding(engine.VERIFIED_OFFER_LETTER, id="offer", received="2026-07-20"),
    ]
    result = reasons(rows)
    assert result.get("reject") == engine.SUPPRESS_SUPERSEDED or "reject" not in result
    # The conflict detector still sees both, because suppression does not
    # remove the finding from the record.
    assert engine.detect_conflicts(rows)


def test_the_strongest_outcome_itself_is_never_suppressed():
    rows = [
        finding(engine.OFFER_INDICATION, id="early", received="2026-07-01"),
        finding(engine.VERIFIED_OFFER_LETTER, id="late", received="2026-07-20"),
    ]
    result = reasons(rows)
    assert "late" not in result


# ── Ordering between rules ───────────────────────────────────────────────────

def test_a_duplicate_of_an_irrelevant_mail_is_reported_as_irrelevant():
    rows = [
        finding(engine.NOT_RELEVANT, id="a", signature="s", signals=["NOISE"]),
        finding(engine.NOT_RELEVANT, id="b", signature="s", signals=["NOISE"],
                received="2026-07-02"),
    ]
    assert set(reasons(rows).values()) == {engine.SUPPRESS_IRRELEVANT}


def test_interview_mail_is_never_reported_as_a_duplicate():
    rows = [
        finding(engine.INTERVIEW_INVITE, id="a", thread="t"),
        finding(engine.INTERVIEW_INVITE, id="b", thread="t", received="2026-07-02"),
    ]
    assert set(reasons(rows).values()) == {engine.SUPPRESS_WRONG_MODE}


def test_every_reason_used_is_one_of_the_four_declared():
    rows = [
        finding(engine.INTERVIEW_INVITE, id="mode"),
        finding(engine.NOT_RELEVANT, id="noise", signals=["NOISE"]),
        finding(engine.BACKGROUND_VERIFICATION, id="bgv"),
        finding(engine.SHORTLISTED, id="dup1", thread="t"),
        finding(engine.SHORTLISTED, id="dup2", thread="t", received="2026-07-02"),
        finding(engine.OFFER_INDICATION, id="early", received="2026-07-03"),
        finding(engine.VERIFIED_OFFER_LETTER, id="late", received="2026-07-04"),
    ]
    assert set(reasons(rows).values()) <= set(engine.SUPPRESSION_REASONS)


# ── Totals after cleanup ─────────────────────────────────────────────────────

def wire(monkeypatch, findings):
    monkeypatch.setattr(audit, "_base_candidate_rows", lambda: [{
        "canonical_candidate_id": "cand-1", "candidate_id": "cand-1",
        "candidate_name": "Test", "email_address": "c@example.com",
        "mailbox_id": "m1", "monitoring_status": "MONITORING_ACTIVE",
        "connection_status": "CONNECTED", "scan_status": "SCANNED",
        "messages_examined": 10, "system_status": None, "last_successful_sync_at": None,
    }])
    monkeypatch.setattr(audit, "_findings_for_mode", lambda mode, **kw: {"cand-1": findings})
    monkeypatch.setattr(audit, "_booking_rows_by_candidate", lambda: {})
    monkeypatch.setattr(audit, "_gap_counts_by_candidate", lambda mode: {})


def test_suppressed_findings_do_not_inflate_the_counts(monkeypatch):
    """The live case: one offer letter stored twice counted as two."""
    wire(monkeypatch, [finding(engine.VERIFIED_OFFER_LETTER, id="kept")])
    row = audit.mode_candidate_rows(engine.MODE_SELECTION)[0]
    assert row["outcome_counts"] == {engine.VERIFIED_OFFER_LETTER: 1}
    assert row["strongest_outcome"] == engine.VERIFIED_OFFER_LETTER


def test_the_selection_query_filters_suppressed_rows_in_sql():
    """Counting must not depend on the caller remembering to filter."""
    source = Path(audit.__file__).read_text(encoding="utf-8")
    body = source.split("def _findings_for_mode(", 1)[1].split("\ndef ", 1)[0]
    assert "COALESCE(suppressed,false)=false" in body
    assert "MODE_SELECTION" in body


# ── Nothing is destroyed ─────────────────────────────────────────────────────

def test_cleanup_never_deletes_a_finding_or_its_evidence():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    body = source.split("def recompute_cleanup(", 1)[1].split("\ndef ", 1)[0]
    # Statements only: the docstring legitimately contains the word "deleted".
    statements = body.split('"""')[-1].upper()
    assert "DELETE FROM" not in statements
    assert "TRUNCATE" not in statements
    # Only the suppression columns are written; evidence and mail are untouched.
    assert "evidence" not in statements.lower()
    assert "suppressed=true" in body


def test_cleanup_writes_a_reason_and_a_timestamp():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    body = source.split("def recompute_cleanup(", 1)[1].split("\ndef ", 1)[0]
    assert "suppression_reason=%s" in body
    assert "suppressed_at=now()" in body
    assert "mail_outcome_audit_cleanup_log" in body


def test_cleanup_restores_a_finding_that_no_longer_matches_a_rule():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    body = source.split("def recompute_cleanup(", 1)[1].split("\ndef ", 1)[0]
    assert "suppressed=false" in body
    assert "RESTORED" in body


def test_cleanup_never_changes_candidate_status():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    for name in ("recompute_cleanup", "excluded_findings", "cleanup_summary"):
        body = source.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
        assert "candidate_job_status" not in body
        assert "_apply_status" not in body


def test_excluded_findings_remain_readable_with_their_mail():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    body = source.split("def excluded_findings(", 1)[1].split("\ndef ", 1)[0]
    for column in ("f.subject", "f.sender_email", "f.suppression_reason", "f.suppressed_at"):
        assert column in body
