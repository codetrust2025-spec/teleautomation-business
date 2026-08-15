"""Replacing and archiving evidence, and the history that records both."""
import pytest

from features import candidate_store as cs
from features import payment_evidence_history as history
from features import payment_evidence_store as pes
from features import payment_receipts
from features import payment_verification_engine as pve


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "a.json"))
    monkeypatch.setenv("PAYMENT_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "led.json"))
    pve._save_ledger({
        "schema_version": 2, "evidence": [], "entitlements": [],
        "payments": [{
            "payment_id": "pay_x", "idempotency_key": "txn:250859628039",
            "evidence_id": "ev_x", "verification_state": "PENDING_MANUAL_REVIEW",
            "amount_minor": 300000, "transaction_reference": "250859628039",
            "transaction_references": {"utr_number": "250859628039"},
            "receiver_type": "company", "receiver_registry_name": "J Ravinder",
            "purpose": "candidate_payment", "candidate_id": "c1",
            "file_availability": "MISSING_FILE",
        }],
        "entries": [{"ledger_entry_id": "le_x", "payment_id": "pay_x",
                     "status": "posted"}],
    })
    row = cs.create_candidate({"name": "Sakthivek Test", "phone": "9000000021",
                               "reference": "Thrilok", "expected_payment": 50000,
                               "payment": 30000})
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == row["id"]:
            item["payment"] = 30000
            item["payment_proofs"] = [{
                "id": "proof-1", "attachment_type": "payment_proof",
                "verified_amount": 30000,
                "verification_state": "VERIFIED_COMPANY_PAYMENT",
                "utr_number": "250859628039", "sha256": "oldchecksum",
                "original_name": "original.jpg", "payment_id": "pay_x",
                "file_availability": "MISSING_FILE",
                "uploaded_at": "2026-06-22T14:36:30+00:00",
            }]
    cs._save(data)
    return row["id"]


# -- archive -----------------------------------------------------------------

def test_archiving_records_the_change_without_moving_money(env):
    before = cs.get_candidate(env)["payment"]
    out = cs.set_proof_file_availability(
        env, "proof-1", pes.ARCHIVED, "file never retained", "administrator")
    assert out["file_availability"] == "ARCHIVED"
    entry = out["file_availability_history"][0]
    assert entry["previous"] == "MISSING_FILE"
    assert entry["new"] == "ARCHIVED"
    assert entry["reviewer"] == "administrator"
    assert cs.get_candidate(env)["payment"] == before


def test_archiving_leaves_the_verified_amount_alone(env):
    cs.set_proof_file_availability(
        env, "proof-1", pes.ARCHIVED, "r", "administrator")
    proof = cs.get_candidate(env)["payment_proofs"][0]
    assert proof["verified_amount"] == 30000
    assert proof["verification_state"] == "VERIFIED_COMPANY_PAYMENT"


def test_archiving_is_idempotent(env):
    cs.set_proof_file_availability(env, "proof-1", pes.ARCHIVED, "r", "admin")
    cs.set_proof_file_availability(env, "proof-1", pes.ARCHIVED, "r", "admin")
    proof = cs.get_candidate(env)["payment_proofs"][0]
    assert len(proof["file_availability_history"]) == 1


def test_an_archived_proof_no_longer_blocks_the_total(env):
    """ARCHIVED is a deliberate retirement, not damage, so it keeps counting."""
    cs.set_proof_file_availability(env, "proof-1", pes.ARCHIVED, "r", "admin")
    proof = cs.get_candidate(env)["payment_proofs"][0]
    assert payment_receipts.proof_status(proof) == payment_receipts.PROOF_STATUS_VERIFIED


# -- replacement -------------------------------------------------------------

def test_replacement_points_the_proof_at_new_evidence_and_keeps_the_old(env):
    stored = pes.store(b"the re-uploaded screenshot", mime_type="image/jpeg",
                       original_filename="reupload.jpg", candidate_id=env,
                       proof_id="proof-1", upload_source="payment_proof_replacement")
    out = cs.apply_replacement_proof(env, "proof-1", {
        "sha256": stored["sha256"], "storage_key": stored["storage_key"],
        "original_name": "reupload.jpg", "verified_amount": 30000,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "file_availability": pes.AVAILABLE,
    }, "original was never retained", "administrator")

    assert out["sha256"] == stored["sha256"]
    assert out["file_availability"] == "AVAILABLE"
    superseded = out["replacement_history"][0]
    assert superseded["previous_checksum"] == "oldchecksum"
    assert superseded["previous_filename"] == "original.jpg"
    assert superseded["reviewer"] == "administrator"
    assert out["id"] == "proof-1", "the proof keeps its id so references resolve"


def test_replacement_creates_no_second_payment_or_credit(env):
    before = pve._load_ledger()
    stored = pes.store(b"replacement", mime_type="image/jpeg", candidate_id=env)
    cs.apply_replacement_proof(env, "proof-1", {
        "sha256": stored["sha256"], "verified_amount": 30000,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "file_availability": pes.AVAILABLE,
    }, "r", "admin")
    cs.recalculate_received_total(env, trigger="proof_replaced", reason="r",
                                  reviewer="admin")
    after = pve._load_ledger()
    assert len(after["payments"]) == len(before["payments"]) == 1
    assert len(after["entries"]) == len(before["entries"]) == 1
    assert cs.get_candidate(env)["payment"] == 30000


def test_replacing_with_the_identical_file_is_deduplicated(env):
    first = pes.store(b"same bytes", mime_type="image/jpeg", candidate_id=env)
    second = pes.store(b"same bytes", mime_type="image/jpeg", candidate_id=env)
    assert second["deduplicated"] is True
    assert first["sha256"] == second["sha256"]
    assert pes.health_report()["record_count"] == 1


def test_replacement_restores_a_row_to_counting_again(env):
    """A MISSING_FILE proof counts nothing; once replaced it counts again."""
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == env:
            item["payment_proofs"][0]["file_availability"] = "MISSING_FILE"
    cs._save(data)
    assert cs.get_candidate(env)["verified_proof_total"] == 0

    stored = pes.store(b"restored", mime_type="image/jpeg", candidate_id=env)
    cs.apply_replacement_proof(env, "proof-1", {
        "sha256": stored["sha256"], "verified_amount": 30000,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "file_availability": pes.AVAILABLE,
    }, "r", "admin")
    assert cs.get_candidate(env)["verified_proof_total"] == 30000


def test_replacing_an_unknown_proof_returns_none(env):
    assert cs.apply_replacement_proof(env, "nope", {"sha256": "x"}, "r", "a") is None


def test_a_legacy_stored_proof_can_be_replaced(env):
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == env:
            item["proofs"] = [dict(item["payment_proofs"][0], id="legacy-1",
                                   legacy_storage=True)]
            item["payment_proofs"] = []
    cs._save(data)
    out = cs.apply_replacement_proof(env, "legacy-1", {"sha256": "new"}, "r", "admin")
    assert out is not None
    stored = next(r for r in cs._load()["candidates"] if r["id"] == env)
    assert stored["proofs"][0]["sha256"] == "new"
    assert stored["payment_proofs"] == [], "it stays in the list that holds it"


# -- history -----------------------------------------------------------------

def test_history_gathers_every_category_in_order(env):
    stored = pes.store(b"replacement bytes", mime_type="image/jpeg",
                       candidate_id=env, proof_id="proof-1",
                       transaction_reference="250859628039")
    pes.link_replacement(original_checksum="oldchecksum",
                         replacement_checksum=stored["sha256"],
                         reviewer="administrator", reason="original lost")
    cs.correct_proof_amount(env, "proof-1", corrected_amount=30000,
                            reason="factor-of-ten", reviewer="admin")
    cs.apply_replacement_proof(env, "proof-1", {"sha256": stored["sha256"]},
                               "original lost", "administrator")
    cs.set_proof_file_availability(env, "proof-1", pes.AVAILABLE, "restored", "admin")
    pve.quarantine_payment(transaction_reference="250859628039",
                           file_state=pve.FILE_MISSING, reason="lost",
                           reviewer="admin")
    pve.add_corroborating_evidence(transaction_reference="250859628039",
                                   description="admin screenshot",
                                   supplied_by="administrator",
                                   stated_amount=30000)

    result = history.proof_history(cs.get_candidate(env), "proof-1")
    kinds = {event["kind"] for event in result["events"]}
    assert "uploaded" in kinds
    assert "file_availability_changed" in kinds
    assert "verification_changed" in kinds
    assert "corroborating_evidence" in kinds
    assert "replaced" in kinds
    timestamps = [e["at"] for e in result["events"] if e["at"]]
    assert timestamps == sorted(timestamps), "events read in order"


def test_history_reports_identity_and_linkage(env):
    result = history.proof_history(cs.get_candidate(env), "proof-1")
    assert result["proof_id"] == "proof-1"
    assert result["utr_number"] == "250859628039"
    assert result["payment_id"] == "pay_x"
    assert result["stored_in"] == "payment_proofs"
    assert result["verified_amount"] == 30000


def test_history_of_an_unknown_proof_is_none(env):
    assert history.proof_history(cs.get_candidate(env), "nope") is None


def test_history_works_for_legacy_stored_proofs(env):
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == env:
            item["proofs"] = [dict(item["payment_proofs"][0], id="legacy-1",
                                   legacy_storage=True)]
            item["payment_proofs"] = []
    cs._save(data)
    result = history.proof_history(cs.get_candidate(env), "legacy-1")
    assert result is not None
    assert result["stored_in"] == "proofs"
