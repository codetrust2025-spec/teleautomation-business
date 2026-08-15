"""Quarantine, file availability, and corroborating evidence."""
import pytest

from features import payment_receipts as receipts
from features import payment_verification_engine as pve


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "led.json"))
    pve._save_ledger({
        "schema_version": 2, "evidence": [], "entitlements": [],
        "payments": [{
            "payment_id": "pay_k", "idempotency_key": "txn:784453317186",
            "evidence_id": "ev_k", "verification_state": "VERIFIED_COMPANY_PAYMENT",
            "amount_minor": 400000, "transaction_reference": "784453317186",
            "transaction_references": {"utr_number": "784453317186"},
            "receiver_type": "company", "receiver_registry_name": "J RAVINDER",
            "purpose": "candidate_payment", "candidate_id": "bccf8ba36d",
        }],
        "entries": [{
            "ledger_entry_id": "le_k", "payment_id": "pay_k",
            "transaction_type": "CANDIDATE_FEE_RECEIVED_BY_COMPANY",
            "action": "company_credit", "status": "posted",
        }],
    })


def test_quarantine_withdraws_trust_and_keeps_the_previous_state(ledger):
    out = pve.quarantine_payment(
        transaction_reference="784453317186", file_state=pve.FILE_MISSING,
        reason="factor-of-ten defect, original screenshot unavailable",
        reviewer="administrator")
    assert out["changed"] is True
    assert out["previous_verification_state"] == "VERIFIED_COMPANY_PAYMENT"

    payment = pve._load_ledger()["payments"][0]
    assert payment["verification_state"] == "AMOUNT_EXTRACTION_REVIEW_REQUIRED"
    assert payment["file_availability"] == "MISSING_FILE"
    assert payment["blocks_automatic_reconciliation"] is True
    history = payment["quarantine_history"]
    assert len(history) == 1
    assert history[0]["previous_verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert history[0]["reviewer"] == "administrator"


def test_quarantine_never_changes_the_amount_or_identity(ledger):
    pve.quarantine_payment(
        transaction_reference="784453317186", file_state=pve.FILE_MISSING,
        reason="r", reviewer="admin")
    payment = pve._load_ledger()["payments"][0]
    assert payment["amount_minor"] == 400000, "an unreadable amount is not a guess"
    assert payment["transaction_reference"] == "784453317186"
    assert payment["evidence_id"] == "ev_k"


def test_quarantine_stops_a_posted_entry_counting(ledger):
    pve.quarantine_payment(
        transaction_reference="784453317186", file_state=pve.FILE_MISSING,
        reason="r", reviewer="admin")
    entry = pve._load_ledger()["entries"][0]
    assert entry["status"] == "quarantined"
    assert entry["ledger_entry_id"] == "le_k", "the entry is kept, not deleted"


def test_quarantine_is_idempotent(ledger):
    first = pve.quarantine_payment(
        transaction_reference="784453317186", file_state=pve.FILE_MISSING,
        reason="r", reviewer="admin")
    second = pve.quarantine_payment(
        transaction_reference="784453317186", file_state=pve.FILE_MISSING,
        reason="r", reviewer="admin")
    assert first["changed"] is True
    assert second["changed"] is False
    assert len(pve._load_ledger()["payments"][0]["quarantine_history"]) == 1


def test_unknown_file_state_is_refused(ledger):
    with pytest.raises(ValueError, match="Unknown file availability state"):
        pve.quarantine_payment(
            transaction_reference="784453317186", file_state="GONE",
            reason="r", reviewer="admin")


def test_marking_a_missing_file_leaves_the_verdict_alone(ledger):
    out = pve.mark_file_availability(
        transaction_reference="784453317186", file_state=pve.FILE_MISSING,
        reason="original upload not retained", reviewer="admin")
    assert out["changed"] is True
    payment = pve._load_ledger()["payments"][0]
    assert payment["file_availability"] == "MISSING_FILE"
    assert payment["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert payment["blocks_automatic_reconciliation"] is True


def test_corroborating_evidence_is_kept_apart_from_system_capture(ledger):
    out = pve.add_corroborating_evidence(
        transaction_reference="784453317186",
        description="Administrator screenshot showing Rs 40,000 twice",
        supplied_by="administrator", stated_amount=40000)
    assert out["changed"] is True
    payment = pve._load_ledger()["payments"][0]
    record = payment["corroborating_evidence"][0]
    assert record["source"] == "ADMINISTRATOR_CORROBORATING_EVIDENCE"
    assert record["is_original_system_capture"] is False
    assert record["stated_amount"] == 40000
    assert payment["amount_minor"] == 400000, "testimony never becomes the amount"
    assert payment["verification_state"] == "VERIFIED_COMPANY_PAYMENT"


def test_corroborating_evidence_is_not_duplicated(ledger):
    for _ in range(3):
        pve.add_corroborating_evidence(
            transaction_reference="784453317186", description="same note",
            supplied_by="administrator")
    assert len(pve._load_ledger()["payments"][0]["corroborating_evidence"]) == 1


# -- receipt layer -----------------------------------------------------------

def proof(**changes):
    value = {
        "id": "p1", "verified_amount": 40000,
        "verification_state": "VERIFIED_COMPANY_PAYMENT", "utr_number": "U1",
    }
    value.update(changes)
    return value


@pytest.mark.parametrize("state", ["MISSING_FILE", "CHECKSUM_MISMATCH", "UNREADABLE"])
def test_unreadable_evidence_cannot_count_towards_the_total(state):
    row = proof(file_availability=state)
    assert receipts.proof_status(row) == receipts.PROOF_STATUS_NEEDS_REVIEW
    assert receipts.proof_amount(row) == 0
    assert receipts.verified_proof_total([row]) == 0


@pytest.mark.parametrize("state", ["AVAILABLE", "ARCHIVED"])
def test_readable_or_archived_evidence_still_counts(state):
    row = proof(file_availability=state)
    assert receipts.proof_status(row) == receipts.PROOF_STATUS_VERIFIED
    assert receipts.verified_proof_total([row]) == 40000


def test_a_quarantined_proof_is_excluded():
    row = proof(blocks_automatic_reconciliation=True)
    assert receipts.proof_status(row) == receipts.PROOF_STATUS_NEEDS_REVIEW
    assert receipts.verified_proof_total([row]) == 0


def test_the_extraction_review_state_needs_a_human():
    row = proof(verification_state="AMOUNT_EXTRACTION_REVIEW_REQUIRED")
    assert receipts.proof_status(row) == receipts.PROOF_STATUS_NEEDS_REVIEW
    assert receipts.verified_proof_total([row]) == 0


def test_missing_evidence_never_silently_becomes_zero_received():
    """A row keeps its recorded amount when its only proof becomes unreadable."""
    summary = receipts.receipt_summary(
        expected=40000, recorded=40000,
        proofs=[proof(file_availability="MISSING_FILE")])
    assert summary["verified_received"] == 40000
    assert summary["verified_proof_total"] == 0
    assert summary["needs_reconciliation"] is True
