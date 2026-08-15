"""Correcting a mis-extracted proof amount, without duplicating credit."""
import pytest

from features import candidate_store as cs
from features import payment_verification_engine as pve


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "led.json"))
    data = {
        "schema_version": 2, "evidence": [], "entitlements": [],
        "payments": [{
            "payment_id": "pay_1", "idempotency_key": "txn:678487078430",
            "evidence_id": "ev_1", "verification_state": "VERIFIED_COMPANY_PAYMENT",
            "amount_minor": 200000, "transaction_reference": "678487078430",
            "transaction_references": {"utr_number": "678487078430",
                                       "transaction_id": "t2606111736078866007255"},
            "receiver_type": "company", "receiver_registry_name": "J Ravinder",
            "purpose": "candidate_payment", "candidate_id": "fa744f47f4",
        }],
        "entries": [{
            "ledger_entry_id": "le_1", "payment_id": "pay_1",
            "transaction_type": "CANDIDATE_FEE_RECEIVED_BY_COMPANY",
            "action": "company_credit", "status": "posted",
        }],
    }
    pve._save_ledger(data)
    return data


def test_correction_supersedes_the_amount_and_keeps_the_old_one(ledger):
    out = pve.correct_extraction_amount(
        transaction_reference="678487078430", corrected_amount=20000,
        reason="factor-of-ten extractor defect", reviewer="admin",
        extractor_version="388c5602")
    assert out["changed"] is True
    assert out["previous_amount"] == 2000
    assert out["new_amount"] == 20000

    payment = pve._load_ledger()["payments"][0]
    assert payment["amount_minor"] == 2000000
    history = payment["amount_corrections"]
    assert len(history) == 1
    assert history[0]["previous_amount"] == 2000
    assert history[0]["new_amount"] == 20000
    assert history[0]["previous_verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert history[0]["reviewer"] == "admin"


def test_identity_fields_are_never_touched(ledger):
    pve.correct_extraction_amount(
        transaction_reference="678487078430", corrected_amount=20000,
        reason="r", reviewer="admin")
    payment = pve._load_ledger()["payments"][0]
    assert payment["transaction_reference"] == "678487078430"
    assert payment["transaction_references"]["transaction_id"] == (
        "t2606111736078866007255")
    assert payment["evidence_id"] == "ev_1"
    assert payment["receiver_registry_name"] == "J Ravinder"


def test_correction_never_creates_a_second_payment_or_entry(ledger):
    for _ in range(3):
        pve.correct_extraction_amount(
            transaction_reference="678487078430", corrected_amount=20000,
            reason="r", reviewer="admin")
    data = pve._load_ledger()
    assert len(data["payments"]) == 1
    assert len(data["entries"]) == 1


def test_repeating_the_same_correction_is_a_no_op(ledger):
    first = pve.correct_extraction_amount(
        transaction_reference="678487078430", corrected_amount=20000,
        reason="r", reviewer="admin")
    second = pve.correct_extraction_amount(
        transaction_reference="678487078430", corrected_amount=20000,
        reason="r", reviewer="admin")
    assert first["changed"] is True
    assert second["changed"] is False
    assert len(pve._load_ledger()["payments"][0]["amount_corrections"]) == 1


def test_unknown_reference_is_refused(ledger):
    with pytest.raises(ValueError, match="No payment found"):
        pve.correct_extraction_amount(
            transaction_reference="000000000000", corrected_amount=100,
            reason="r", reviewer="admin")


def test_a_non_positive_amount_is_refused(ledger):
    with pytest.raises(ValueError, match="must be positive"):
        pve.correct_extraction_amount(
            transaction_reference="678487078430", corrected_amount=0,
            reason="r", reviewer="admin")


# -- candidate-side proof ---------------------------------------------------

@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "a.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    row = cs.create_candidate({"name": "Ram Charan M S", "phone": "9000000009",
                               "reference": "Pavan Kalyan",
                               "expected_payment": 20000, "payment": 20000})
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == row["id"]:
            item["payment"] = 20000
            item["payment_proofs"] = [{
                "id": "3300acabb4ad", "attachment_type": "payment_proof",
                "verified_amount": 2000,
                "verification_state": "VERIFIED_COMPANY_PAYMENT",
                "utr_number": "678487078430",
                "transaction_id": "T2606111736078866007255",
                "sha256": "4e9c6dd0", "filename": "3300acabb4ad.jpg",
            }]
    cs._save(data)
    return row["id"]


def test_proof_correction_keeps_the_superseded_reading(store):
    out = cs.correct_proof_amount(store, "3300acabb4ad", corrected_amount=20000,
                                  reason="factor-of-ten defect", reviewer="admin",
                                  extractor_version="388c5602")
    assert out["verified_amount"] == 20000
    assert out["amount_corrections"][0]["previous_amount"] == 2000
    assert out["utr_number"] == "678487078430"
    assert out["sha256"] == "4e9c6dd0"


def test_corrected_proof_reconciles_the_row_without_moving_money(store):
    """The recorded amount was already right; only the evidence was wrong, so
    the repair must reconcile them and move nothing."""
    before = cs.get_candidate(store)
    assert before["payment"] == 20000
    assert before["referral_commission"] == 10000
    assert before["verified_proof_total"] == 2000

    cs.correct_proof_amount(store, "3300acabb4ad", corrected_amount=20000,
                            reason="r", reviewer="admin")
    cs.recalculate_received_total(store, trigger="extraction_correction",
                                  reason="r", reviewer="admin")

    after = cs.get_candidate(store)
    assert after["payment"] == 20000
    assert after["referral_commission"] == 10000
    assert after["verified_proof_total"] == 20000
    assert after["verified_proof_count"] == 1
    assert after["payment_is_proof_derived"] is True
    assert after["payment_unevidenced"] is False


def test_repeated_recalculation_stays_idempotent(store):
    cs.correct_proof_amount(store, "3300acabb4ad", corrected_amount=20000,
                            reason="r", reviewer="admin")
    for _ in range(3):
        cs.recalculate_received_total(store, trigger="extraction_correction",
                                      reason="r", reviewer="admin")
    row = cs.get_candidate(store)
    assert row["payment"] == 20000
    assert cs.handler_earning_allocations(row) == {"pavan kalyan": 10000}


def test_correcting_the_same_proof_twice_does_not_stack_history(store):
    cs.correct_proof_amount(store, "3300acabb4ad", corrected_amount=20000,
                            reason="r", reviewer="admin")
    out = cs.correct_proof_amount(store, "3300acabb4ad", corrected_amount=20000,
                                  reason="r", reviewer="admin")
    assert len(out["amount_corrections"]) == 1


def test_unknown_proof_returns_none(store):
    assert cs.correct_proof_amount(store, "nope", corrected_amount=1,
                                   reason="r", reviewer="admin") is None


@pytest.fixture()
def legacy_store(tmp_path, monkeypatch):
    """A proof stored in the pre-typed `proofs` list, as the production record
    for Ram Charan is."""
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "a.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    row = cs.create_candidate({"name": "Legacy Proof", "phone": "9000000010",
                               "reference": "Pavan Kalyan",
                               "expected_payment": 20000, "payment": 20000})
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == row["id"]:
            item["payment"] = 20000
            item["payment_proofs"] = []
            item["proofs"] = [{
                "id": "3300acabb4ad", "attachment_type": "payment_proof",
                "legacy_storage": True, "verified_amount": 2000,
                "verification_state": "VERIFIED_COMPANY_PAYMENT",
                "utr_number": "678487078430", "sha256": "4e9c6dd0",
                "filename": "3300acabb4ad.jpg",
            }]
    cs._save(data)
    return row["id"]


def test_a_legacy_stored_proof_can_be_corrected(legacy_store):
    out = cs.correct_proof_amount(legacy_store, "3300acabb4ad",
                                  corrected_amount=20000, reason="r",
                                  reviewer="admin")
    assert out is not None, "legacy `proofs` storage must be searched too"
    assert out["verified_amount"] == 20000
    assert out["amount_corrections"][0]["previous_amount"] == 2000

    stored = next(r for r in cs._load()["candidates"] if r["id"] == legacy_store)
    assert stored["proofs"][0]["verified_amount"] == 20000
    assert stored["payment_proofs"] == [], "it must not be moved between lists"


def test_legacy_correction_reconciles_the_row(legacy_store):
    cs.correct_proof_amount(legacy_store, "3300acabb4ad", corrected_amount=20000,
                            reason="r", reviewer="admin")
    cs.recalculate_received_total(legacy_store, trigger="extraction_correction",
                                  reason="r", reviewer="admin")
    row = cs.get_candidate(legacy_store)
    assert row["payment"] == 20000
    assert row["verified_proof_total"] == 20000
    assert row["referral_commission"] == 10000
