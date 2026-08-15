"""Controlled first rollout: what gets reviewed, and whether to believe it.

A second opinion is only worth having if it can be checked. These tests cover
the eligibility filter that keeps the first batch small and targeted, and the
verification that catches a model citing a message, quote or attachment that
was never in front of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_audit_ai as audit_ai  # noqa: E402
from core import recruitment_mail_audit as engine  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


# ── Eligibility: a targeted first pass, not a backfill ───────────────────────

def test_eligibility_covers_every_category_the_rollout_asked_for():
    sql = audit_ai.ELIGIBILITY_SQL
    assert "MANUAL_REVIEW_REQUIRED" in sql          # rules already unsure
    assert "manual_review_required = true" in sql
    assert "AUDIT_STRONGER" in sql and "PIPELINE_STRONGER" in sql  # disagreements
    assert "VERIFIED_OFFER_LETTER" in sql and "OFFER_INDICATION" in sql  # offers
    assert "SUSPICIOUS" in sql                       # authenticity concerns
    assert "status_mismatch = true" in sql           # status mismatches


def test_eligibility_excludes_suppressed_and_already_reviewed():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def eligible_findings(", 1)[1].split("\n\n\n", 1)[0]
    assert "COALESCE(f.suppressed,false) = false" in body
    assert "mail_audit_ai_queue" in body and "mail_audit_ai_results" in body
    assert "NOT EXISTS" in body


def test_eligibility_is_scoped_to_the_selection_audit():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def eligible_findings(", 1)[1].split("\n\n\n", 1)[0]
    assert "SELECTION_OUTCOMES" in body


def test_the_batch_size_is_bounded():
    """No call can turn the first rollout into a full backfill."""
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def eligible_findings(", 1)[1].split("\n\n\n", 1)[0]
    assert "min(50" in body


def test_offers_are_reviewed_before_lesser_outcomes():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def eligible_findings(", 1)[1].split("\n\n\n", 1)[0]
    assert body.index("'VERIFIED_OFFER_LETTER' THEN 0") < body.index("ELSE 3")


# ── The reviewer must actually read the material ─────────────────────────────

def test_the_prompt_carries_the_whole_thread_and_attachment_text():
    payload = audit_ai._prompt_payload({
        "provider_message_id": "m1", "outcome": "SHORTLISTED", "subject": "s",
        "thread": [{"message_id": "m0", "body": "earlier"},
                   {"message_id": "m1", "body": "later"}],
        "attachments": [{"filename": "offer.pdf", "extracted_text": "APPOINTMENT LETTER"}],
    }, "")
    user = payload[1]["content"]
    assert "earlier" in user and "later" in user
    assert "APPOINTMENT LETTER" in user
    assert "offer.pdf" in user


def test_the_prompt_names_the_application_so_companies_stay_separate():
    payload = audit_ai._prompt_payload({"application_key": "acme.example:engineer"}, "")
    assert "acme.example:engineer" in payload[1]["content"]


def test_the_prompt_instructs_against_invention():
    assert "Do not invent" in audit_ai.AUDIT_SYSTEM_PROMPT
    assert "verbatim" in audit_ai.AUDIT_SYSTEM_PROMPT
    assert "companies separate" in audit_ai.AUDIT_SYSTEM_PROMPT


def test_the_schema_requires_citations():
    required = set(audit_ai.AUDIT_SCHEMA["required"])
    assert {"cited_message_id", "quoted_evidence", "cited_attachment"} <= required


# ── Fabrication is caught, not believed ──────────────────────────────────────

def finding():
    return {
        "provider_message_id": "19f6aeccaff1b324",
        "subject": "Your offer letter",
        "thread": [
            {"message_id": "19f6ad5e1eb33a58", "body": "We would like to schedule a call."},
            {"message_id": "19f6aeccaff1b324", "body": "We are pleased to offer you the role."},
        ],
        "attachments": [
            {"filename": "Offer_Letter.pdf", "extracted_text": "Annual CTC is INR 24,00,000"},
        ],
    }


def answer(**overrides):
    base = {
        "cited_message_id": "19f6aeccaff1b324",
        "quoted_evidence": "We are pleased to offer you the role.",
        "cited_attachment": "Offer_Letter.pdf",
    }
    base.update(overrides)
    return base


def test_a_well_cited_answer_is_trusted():
    result = audit_ai.verify_review(finding(), answer())
    assert result["trusted"] is True
    assert result["problems"] == []


def test_an_invented_message_id_is_caught():
    result = audit_ai.verify_review(finding(), answer(cited_message_id="19f6aeccaff1b999"))
    assert result["trusted"] is False
    assert any("19f6aeccaff1b999" in problem for problem in result["problems"])


def test_an_invented_quotation_is_caught():
    result = audit_ai.verify_review(
        finding(), answer(quoted_evidence="Your joining date is confirmed for Monday."))
    assert result["trusted"] is False
    assert any("does not appear" in problem for problem in result["problems"])


def test_an_invented_attachment_is_caught():
    result = audit_ai.verify_review(
        finding(), answer(cited_attachment="Appointment_Letter_Final.pdf"))
    assert result["trusted"] is False
    assert any("does not exist" in problem for problem in result["problems"])


def test_a_quotation_from_the_attachment_is_accepted():
    result = audit_ai.verify_review(
        finding(), answer(quoted_evidence="Annual CTC is INR 24,00,000"))
    assert result["trusted"] is True


def test_a_missing_citation_is_caught():
    assert audit_ai.verify_review(finding(), answer(cited_message_id=""))["trusted"] is False
    assert audit_ai.verify_review(finding(), answer(quoted_evidence=""))["trusted"] is False


def test_citing_an_attachment_when_there_are_none_is_caught():
    bare = finding()
    bare["attachments"] = []
    result = audit_ai.verify_review(bare, answer())
    assert result["trusted"] is False


def test_quote_matching_ignores_whitespace_and_case():
    result = audit_ai.verify_review(
        finding(), answer(quoted_evidence="  WE ARE PLEASED   to offer you the role. "))
    assert result["trusted"] is True


def test_a_null_attachment_citation_is_allowed():
    result = audit_ai.verify_review(finding(), answer(cited_attachment=None))
    assert result["trusted"] is True


# ── An unverified review is stored, marked, and never acted on ───────────────

def test_verification_is_persisted_with_the_result():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def _store_result(", 1)[1].split("\ndef ", 1)[0]
    assert "verified" in body and "verification_problems" in body


def test_an_unverified_review_defaults_to_untrusted():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def _store_result(", 1)[1].split("\ndef ", 1)[0]
    assert '"trusted": False' in body


def test_the_gateway_result_is_read_from_content_not_data():
    """AIResult exposes .content as a JSON string; there is no .data. Reading
    the wrong attribute failed every review in the first production batch."""
    from core.ai_gateway import AIResult
    assert not hasattr(AIResult, "data")
    assert "content" in AIResult.__dataclass_fields__
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def review_one(", 1)[1].split("\ndef ", 1)[0]
    assert 'getattr(answer, "content"' in body
    assert "json.loads(raw)" in body


def test_the_rollout_still_changes_no_candidate_record():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    for token in ("candidate_job_status", "candidate_status_history",
                  "approve_outcome", "execute_auto_booking"):
        assert token not in source
