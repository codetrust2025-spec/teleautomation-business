"""Hardening from the first production batch.

Every fixture is a real failure observed on 2026-08-05. The batch produced
five reviews; three of them were wrong in a way that would have misled an
administrator, and the model's own self-assessment fields could not be trusted
at all. These tests pin each fix to the case that motivated it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_audit_ai as audit_ai  # noqa: E402
from core import recruitment_mail_audit as engine  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


# ── Automatic processing is paused, manual runs are not ──────────────────────

def test_auto_processing_has_its_own_flag():
    assert audit_ai.AUTO_PROCESSING_FLAG == "AI_MAIL_AUDIT_AUTO_PROCESSING_ENABLED"


def test_auto_processing_defaults_to_off(monkeypatch):
    monkeypatch.delenv("AI_MAIL_AUDIT_AUTO_PROCESSING_ENABLED", raising=False)
    assert audit_ai.auto_processing_enabled() is False


def test_an_unattended_pass_does_nothing_while_paused(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    monkeypatch.setenv("AI_MAIL_AUDIT_AUTO_PROCESSING_ENABLED", "false")
    claimed = []
    monkeypatch.setattr(audit_ai, "claim", lambda limit=1: claimed.append(limit) or [])
    result = audit_ai.process_pending()
    assert result["deferred"] is True
    assert "AUTO_PROCESSING" in result["reason"]
    assert claimed == []  # pending jobs are left exactly as they were


def test_a_manual_run_still_works_while_paused(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    monkeypatch.setenv("AI_MAIL_AUDIT_AUTO_PROCESSING_ENABLED", "false")
    monkeypatch.setattr(audit_ai, "live_pipeline_busy",
                        lambda: {"busy": False, "sync_jobs": 0, "ai_backlog": 0,
                                 "ingestion": 0, "ai_in_backoff": 0})
    monkeypatch.setattr("core.ai_gateway.health",
                        lambda **kw: {"endpoint_reachable": True, "model_available": True})
    monkeypatch.setattr(audit_ai, "ensure_schema", lambda: None)
    monkeypatch.setattr(audit_ai, "claim", lambda limit=1: [])
    result = audit_ai.process_pending(manual=True)
    assert result["deferred"] is False


def test_the_worker_checks_both_flags():
    source = (REPO / "workers" / "recruitment_mail_worker.py").read_text(encoding="utf-8")
    body = source.split("def process_audit_ai(", 1)[1].split("\n    def ", 1)[0]
    assert "audit_ai.enabled()" in body
    assert "audit_ai.auto_processing_enabled()" in body


def test_pausing_the_audit_does_not_touch_booking():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    assert "AI_INTERVIEW_AUTO_BOOKING_ENABLED" not in source.replace(
        "AI_INTERVIEW_AUTO_BOOKING_ENABLED and never read here as a substitute.", "")


# ── Confidence: 1.0, 0.95, 95 and 100 all appeared in one batch ──────────────

@pytest.mark.parametrize(("raw", "expected"), [
    (1.0, 100.0),      # Thummala Karunakar
    (0.95, 95.0),      # Lekkala swathi
    (95.0, 95.0),      # Gopichand
    (100.0, 100.0),    # Manu, Abilash Perla
    (0, 0.0),
    (78, 78.0),
])
def test_confidence_is_normalised_to_a_percentage(raw, expected):
    assert audit_ai.normalize_confidence(raw) == expected


@pytest.mark.parametrize("raw", [-1, -0.5, 101, 1000, "abc", None, float("nan")])
def test_impossible_confidence_is_rejected(raw):
    assert audit_ai.normalize_confidence(raw) is None


def test_confidence_is_not_kept_until_the_citations_verify():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def review_one(", 1)[1].split("\ndef ", 1)[0]
    assert 'if verification["trusted"] else None' in body


# ── Agreement: the model's own field was unusable ────────────────────────────

def test_the_same_outcome_counts_as_agreement_whatever_the_model_claims():
    """Thummala Karunakar: agrees=False with an identical outcome."""
    assert audit_ai.derive_agreement(
        "MANUAL_REVIEW_REQUIRED", "MANUAL_REVIEW_REQUIRED", "MANUAL_REVIEW_REQUIRED",
    ) == "AGREES_WITH_RULES"


def test_a_different_outcome_is_a_disagreement_whatever_the_model_claims():
    """Gopichand: agrees=True while suggesting a different outcome."""
    assert audit_ai.derive_agreement(
        "OFFER_INDICATION", None, "JOINING_CONFIRMED") == "DISAGREES"


def test_agreement_with_the_pipeline_is_reported_distinctly():
    assert audit_ai.derive_agreement(
        "MANUAL_REVIEW_REQUIRED", "JOINING_CONFIRMED", "JOINING_CONFIRMED",
    ) == "AGREES_WITH_PIPELINE"


def test_any_disagreement_requires_review():
    assert audit_ai.agreement_requires_review("DISAGREES") is True
    assert audit_ai.agreement_requires_review("AGREES_WITH_PIPELINE") is True
    assert audit_ai.agreement_requires_review("AGREES_WITH_RULES") is False


def test_the_self_reported_field_is_never_consulted():
    """Naming the field in prose is fine; reading it to decide is not."""
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def derive_agreement(", 1)[1].split("\ndef ", 1)[0]
    statements = body.split('"""')[-1]
    for read in ('get("agrees")', "get('agrees')", '["agrees"]', "['agrees']"):
        assert read not in statements
    # And the derivation takes only the three outcomes as inputs.
    signature = body.split(")", 1)[0]
    assert "deterministic" in signature and "pipeline" in signature and "ollama" in signature
    assert "agrees" not in signature


# ── Fabricated citations ─────────────────────────────────────────────────────

def finding(**overrides):
    base = {
        "provider_message_id": "19f6b02d5051d006",
        "subject": "Documents",
        "sender_email": "hr@acme-corp.example",
        "company_domain": "acme-corp.example",
        "company_name": "Acme Corp",
        "job_title": "Engineer",
        "thread": [{"message_id": "19f6b02d5051d006",
                    "subject": "Documents",
                    "body": "We are pleased to offer you the role."}],
        "attachments": [],
        "owned_addresses": [],
        "forwarded": False,
    }
    base.update(overrides)
    return base


def test_a_payroll_reference_is_rejected_as_a_message_id():
    """Gopichand: the model cited 'Ref:839093/1949677/ELTP 01-SEP-2021'."""
    result = audit_ai.verify_review(finding(), {
        "cited_message_id": "Ref:839093/1949677/ELTP 01-SEP-2021",
        "quoted_evidence": "We are pleased to offer you the role.",
    })
    assert result["trusted"] is False
    assert any("not a Gmail message id" in p for p in result["problems"])


@pytest.mark.parametrize("bogus", [
    "APP-2026-00123", "Ref:12345/678", "ELTP 01-SEP-2021",
    "invoice-99", "candidate_4471",
])
def test_document_references_are_never_accepted_as_message_ids(bogus):
    result = audit_ai.verify_review(finding(), {
        "cited_message_id": bogus,
        "quoted_evidence": "We are pleased to offer you the role.",
    })
    assert result["trusted"] is False


def test_a_quote_must_appear_in_the_message_that_was_cited():
    """Citing message A while quoting message B cannot be followed."""
    value = finding(thread=[
        {"message_id": "19f6b02d5051d006", "subject": "A", "body": "Please share documents."},
        {"message_id": "19f6b02d5051d007", "subject": "B", "body": "You have been selected."},
    ])
    result = audit_ai.verify_review(value, {
        "cited_message_id": "19f6b02d5051d006",
        "quoted_evidence": "You have been selected.",
    })
    assert result["trusted"] is False
    assert any("cited message" in p for p in result["problems"])


def test_an_attachment_checksum_must_exist_in_the_evidence():
    value = finding(attachments=[{"filename": "offer.pdf", "checksum": "abc123",
                                  "extracted_text": "Annual CTC is INR 24,00,000"}])
    result = audit_ai.verify_review(value, {
        "cited_message_id": "19f6b02d5051d006",
        "quoted_evidence": "Annual CTC is INR 24,00,000",
        "cited_attachment": "offer.pdf",
        "cited_attachment_checksum": "not-a-real-checksum",
    })
    assert result["trusted"] is False
    assert any("checksum" in p for p in result["problems"])


def test_a_fully_grounded_citation_passes():
    value = finding(attachments=[{"filename": "offer.pdf", "checksum": "abc123",
                                  "extracted_text": "Annual CTC is INR 24,00,000"}])
    result = audit_ai.verify_review(value, {
        "cited_message_id": "19f6b02d5051d006",
        "quoted_evidence": "Annual CTC is INR 24,00,000",
        "cited_attachment": "offer.pdf",
        "cited_attachment_checksum": "abc123",
    })
    assert result["trusted"] is True


# ── Sender authenticity is computed, not asserted ────────────────────────────

def test_the_operator_mailbox_is_not_company_confirmation():
    """Abilash Perla: 'You Have Been Selected' from codetrust2025@gmail.com,
    which the model called the hiring company."""
    result = audit_ai.sender_is_hiring_company(
        finding(sender_email="codetrust2025@gmail.com",
                owned_addresses=["codetrust2025@gmail.com"]),
        "candidate@gmail.com")
    assert result["is_company"] is False
    assert any("own" in reason.lower() or "owned" in reason.lower()
               for reason in result["reasons"])


def test_the_candidates_own_mailbox_is_not_company_confirmation():
    result = audit_ai.sender_is_hiring_company(
        finding(sender_email="candidate@gmail.com"), "candidate@gmail.com")
    assert result["is_company"] is False


@pytest.mark.parametrize("sender", [
    "recruiter@gmail.com", "hr@outlook.com", "talent@yahoo.com", "x@hotmail.com",
])
def test_free_mail_is_never_company_proof(sender):
    result = audit_ai.sender_is_hiring_company(finding(sender_email=sender))
    assert result["is_company"] is False
    assert any("free-mail" in reason for reason in result["reasons"])


def test_a_job_portal_is_never_company_proof():
    result = audit_ai.sender_is_hiring_company(
        finding(sender_email="alerts@jobs.shine.com"))
    assert result["is_company"] is False


def test_a_forwarded_mail_is_not_trusted_without_the_original_sender():
    result = audit_ai.sender_is_hiring_company(
        finding(sender_email="hr@acme-corp.example", forwarded=True))
    assert result["is_company"] is False
    assert any("Forwarded" in reason for reason in result["reasons"])


def test_a_matching_company_domain_is_confirmation():
    result = audit_ai.sender_is_hiring_company(finding())
    assert result["is_company"] is True


# ── Evidence restrictions ────────────────────────────────────────────────────

PAYSLIP = ("Employee Code : 782541 Pay Period : 01/04/2026 To 30/04/2026 "
           "Employee Name : Gumma Gopichand Net Pay 84,500 Provident Fund")


def test_a_payslip_cannot_prove_joining():
    """Gopichand: the model read JOINING_CONFIRMED off a Tech Mahindra payslip."""
    value = finding(attachments=[{"filename": "30-APR-2026.pdf", "checksum": "c1",
                                  "extracted_text": PAYSLIP}])
    result = audit_ai.apply_evidence_restrictions(
        value, {"suggested_outcome": "JOINING_CONFIRMED"},
        provenance={"is_company": True, "reasons": []})
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED
    assert any("payslip" in r.lower() for r in result["restrictions"])


@pytest.mark.parametrize("outcome", [
    "FINAL_SELECTION", "VERIFIED_OFFER_LETTER", "JOINING_CONFIRMED"])
def test_no_high_stakes_outcome_survives_a_payslip(outcome):
    value = finding(attachments=[{"filename": "p.pdf", "checksum": "c",
                                  "extracted_text": PAYSLIP}])
    result = audit_ai.apply_evidence_restrictions(
        value, {"suggested_outcome": outcome},
        provenance={"is_company": True, "reasons": []})
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED


def test_a_forum_welcome_is_not_joining_confirmation():
    """Manu: 'Welcome Aboard!' from the Oracle University community."""
    value = finding(
        subject="Welcome Aboard!",
        sender_email="ou.oracle@vanillaforums.email",
        company_domain=None, company_name="Oracle",
        thread=[{"message_id": "19fcd4f1d8aa3434", "subject": "Welcome Aboard!",
                 "body": "We are excited for you to join the Oracle Training and "
                         "Certification Community. In this community you will find "
                         "discussion boards. Unsubscribe here."}])
    result = audit_ai.apply_evidence_restrictions(
        value, {"suggested_outcome": "JOINING_CONFIRMED"},
        provenance={"is_company": False, "reasons": ["free-mail"]})
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED
    assert any("community" in r.lower() or "forum" in r.lower()
               for r in result["restrictions"])


def test_a_self_sent_selection_mail_goes_to_manual_review():
    """Abilash Perla's selection mail from the operator's own address."""
    result = audit_ai.apply_evidence_restrictions(
        finding(sender_email="codetrust2025@gmail.com"),
        {"suggested_outcome": "FINAL_SELECTION"},
        provenance={"is_company": False,
                    "reasons": ["Sent from a TeleAutomation-owned mailbox."]})
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED


def test_a_bulk_campaign_cannot_establish_a_round_or_an_offer():
    result = audit_ai.apply_evidence_restrictions(
        finding(), {"suggested_outcome": "NEXT_ROUND", "is_bulk_campaign": True},
        provenance={"is_company": True, "reasons": []})
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED


def test_an_outcome_without_a_role_or_company_is_not_accepted():
    for missing in ({"job_title": ""}, {"company_name": "", "company_domain": ""}):
        result = audit_ai.apply_evidence_restrictions(
            finding(**missing), {"suggested_outcome": "VERIFIED_OFFER_LETTER"},
            provenance={"is_company": True, "reasons": []})
        assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED


def test_a_genuine_company_offer_survives_every_restriction():
    value = finding(attachments=[{
        "filename": "Offer_Letter.pdf", "checksum": "c1",
        "extracted_text": "APPOINTMENT LETTER. We are pleased to offer you the "
                          "position. Your annual CTC is INR 24,00,000."}])
    result = audit_ai.apply_evidence_restrictions(
        value, {"suggested_outcome": "VERIFIED_OFFER_LETTER"},
        provenance={"is_company": True, "reasons": []})
    assert result["outcome"] == "VERIFIED_OFFER_LETTER"
    assert result["restrictions"] == []


# ── Approval safety ──────────────────────────────────────────────────────────

def test_an_unverified_review_is_never_approvable():
    state = audit_ai.approval_state(
        verified=False, agreement="AGREES_WITH_RULES", restrictions=[])
    assert state == audit_ai.AI_NOT_APPROVABLE
    assert state == "AI suggestion — not eligible for approval."


def test_a_restricted_review_is_never_approvable():
    assert audit_ai.approval_state(
        verified=True, agreement="AGREES_WITH_RULES",
        restrictions=["payslip"]) == audit_ai.AI_NOT_APPROVABLE


def test_a_disagreement_requires_manual_review():
    assert audit_ai.approval_state(
        verified=True, agreement="DISAGREES",
        restrictions=[]) == audit_ai.NEEDS_MANUAL_REVIEW


def test_only_verified_agreeing_unrestricted_evidence_is_safe_to_review():
    assert audit_ai.approval_state(
        verified=True, agreement="AGREES_WITH_RULES",
        restrictions=[]) == audit_ai.SAFE_TO_REVIEW


def test_the_audit_ai_still_cannot_change_a_status_or_a_booking():
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    for token in ("candidate_job_status", "candidate_status_history",
                  "approve_outcome", "execute_auto_booking",
                  "execute_manual_approved_booking", "assign_interview_slot"):
        assert token not in source
