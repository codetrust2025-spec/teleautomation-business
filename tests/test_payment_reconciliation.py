"""System-wide reconciliation: classification, safety, and idempotence."""
import pytest

from features import candidate_store as cs
from features import payment_reconciliation as recon
from features import payment_verification_engine as pve


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "led.json"))
    pve._save_ledger({"schema_version": 2, "evidence": [], "entitlements": [],
                      "payments": [], "entries": []})
    return tmp_path


def add(name, phone, *, payment, expected=20000, proofs=(), bgv=False):
    row = cs.create_candidate({"name": name, "phone": phone,
                               "reference": "Thrilok",
                               "expected_payment": expected, "payment": payment})
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == row["id"]:
            item["payment"] = payment
            item["bgv_certificates"] = bgv
            item["payment_proofs"] = [dict(p) for p in proofs]
    cs._save(data)
    return row["id"]


def proof(amount, *, pid, utr, state="VERIFIED_COMPANY_PAYMENT", file_state=None):
    row = {"id": pid, "attachment_type": "payment_proof",
           "verified_amount": amount, "verification_state": state,
           "utr_number": utr}
    if file_state:
        row["file_availability"] = file_state
    return row


def find(records, name):
    return next(r for r in records if r["candidate_name"] == name)


def test_matching_evidence_is_an_exact_match(env):
    add("Exact", "9000000101", payment=20000,
        proofs=[proof(20000, pid="p1", utr="U1")])
    record = find(recon.profile_rows(), "Exact")
    assert record["classification"] == recon.EXACT_MATCH
    assert record["difference"] == 0


def test_evidence_above_the_recorded_amount_is_safe_to_correct(env):
    add("Under Recorded", "9000000102", payment=5000, expected=5000,
        proofs=[proof(6000, pid="p1", utr="U1")])
    record = find(recon.profile_rows(), "Under Recorded")
    assert record["classification"] == recon.SAFE_AUTOMATIC_CORRECTION
    assert record["difference"] == 1000
    assert record["auto_correctable"] is True


def test_evidence_below_the_recorded_amount_is_never_auto_correctable(env):
    """Almost always uncaptured evidence rather than overstated revenue."""
    add("Short Evidence", "9000000103", payment=20000,
        proofs=[proof(2000, pid="p1", utr="U1")])
    record = find(recon.profile_rows(), "Short Evidence")
    assert record["classification"] == recon.GENUINE_MISMATCH
    assert record["auto_correctable"] is False
    assert "Do not reduce" in record["recommended_action"]


def test_money_with_no_adjudicated_proof_is_legacy_not_a_mismatch(env):
    add("Legacy", "9000000104", payment=20000)
    record = find(recon.profile_rows(), "Legacy")
    assert record["classification"] == recon.LEGACY_INCOMPLETE_COVERAGE
    assert record["auto_correctable"] is False


def test_legacy_money_is_never_proposed_for_zeroing(env):
    add("Legacy", "9000000105", payment=20000)
    record = find(recon.profile_rows(), "Legacy")
    assert record["recorded_received"] == 20000
    assert "Leave as is" in record["recommended_action"]


def test_unreadable_evidence_is_missing_evidence(env):
    add("Broken File", "9000000106", payment=20000,
        proofs=[proof(20000, pid="p1", utr="U1", file_state="MISSING_FILE")])
    record = find(recon.profile_rows(), "Broken File")
    assert record["classification"] == recon.MISSING_EVIDENCE
    assert record["auto_correctable"] is False


def test_a_duplicated_reference_counts_once_and_is_flagged(env):
    add("Duplicate", "9000000107", payment=20000, proofs=[
        proof(20000, pid="p1", utr="U9"), proof(20000, pid="p2", utr="U9")])
    record = find(recon.profile_rows(), "Duplicate")
    assert record["classification"] == recon.DUPLICATE_TRANSACTION
    assert record["duplicate_references"] == 1
    assert record["verified_transaction_total"] == 20000, "one credit only"


def test_excess_beyond_both_obligations_requires_review(env):
    add("Overpaid", "9000000108", payment=60000, expected=50000, bgv=True,
        proofs=[proof(60000, pid="p1", utr="U1")])
    record = find(recon.profile_rows(), "Overpaid")
    assert record["classification"] == recon.UNALLOCATED_EXCESS
    assert record["unallocated_excess"] == 10000
    assert record["auto_correctable"] is False


def test_an_admin_voided_payment_is_noted_not_used_as_the_label(env):
    cid = add("Voided", "9000000109", payment=30000)
    pve._save_ledger({
        "schema_version": 2, "evidence": [], "entitlements": [], "entries": [],
        "payments": [{"payment_id": "pay_v", "transaction_reference": "U404",
                      "amount_minor": 200000, "candidate_id": cid,
                      "verification_state": "REJECTED",
                      "admin_disposition": "ADMIN_CONFIRMED_NOT_PAID"}],
    })
    record = find(recon.profile_rows(), "Voided")
    # The void is history, not the profile's current position: this row's money
    # predates proof capture, and that is what the classification must say.
    assert record["classification"] == recon.LEGACY_INCOMPLETE_COVERAGE
    assert record["has_historical_void"] is True
    assert record["admin_voided_payments"] == 1
    assert any("ADMIN_CONFIRMED_NOT_PAID" in note for note in record["notes"])
    assert record["auto_correctable"] is False


def test_bgv_allocation_is_reported_separately(env):
    add("BGV Split", "9000000110", payment=30000, expected=50000, bgv=True,
        proofs=[proof(30000, pid="p1", utr="U1")])
    record = find(recon.profile_rows(), "BGV Split")
    assert record["service_expected"] == 20000
    assert record["service_allocation"] == 20000
    assert record["bgv_allocation"] == 10000
    assert record["bgv_outstanding"] == 20000
    assert record["referral"] == 10000
    assert record["company_share"] == 10000


def test_clone_rows_reconcile_as_one_profile(env):
    cid = add("Cloned", "9000000111", payment=20000,
              proofs=[proof(20000, pid="shared", utr="U1")])
    data = cs._load()
    original = next(r for r in data["candidates"] if r["id"] == cid)
    for index in range(3):
        clone = dict(original, id=f"clone{index}")
        data["candidates"].append(clone)
    cs._save(data)
    records = [r for r in recon.profile_rows() if r["candidate_name"] == "Cloned"]
    assert len(records) == 1, "one profile, not four rows"
    assert records[0]["verified_transaction_total"] == 20000


def test_preview_summarises_without_writing(env):
    add("Exact", "9000000112", payment=20000,
        proofs=[proof(20000, pid="p1", utr="U1")])
    add("Legacy", "9000000113", payment=15000)
    before = cs._load()
    out = recon.preview()
    assert out["profiles_checked"] == 2
    assert out["counts"][recon.EXACT_MATCH] == 1
    assert out["counts"][recon.LEGACY_INCOMPLETE_COVERAGE] == 1
    assert cs._load() == before, "preview must not change anything"


def test_preview_is_idempotent(env):
    add("Exact", "9000000114", payment=20000,
        proofs=[proof(20000, pid="p1", utr="U1")])
    assert recon.preview()["records"] == recon.preview()["records"]


def test_csv_export_carries_the_decision_columns(env):
    add("Exact", "9000000115", payment=20000,
        proofs=[proof(20000, pid="p1", utr="U1")])
    csv_text = recon.csv_rows(recon.profile_rows())
    header = csv_text.splitlines()[0]
    for column in ("classification", "verified_transaction_total",
                   "service_allocation", "bgv_allocation", "recommended_action"):
        assert column in header
    assert "Exact" in csv_text


def test_a_historical_void_does_not_replace_a_matching_profile(env):
    """Someone confirming months ago that a payment never happened says nothing
    about whether the profile balances today."""
    cid = add("Voided But Balanced", "9000000116", payment=20000,
              proofs=[proof(20000, pid="p1", utr="U1")])
    pve._save_ledger({
        "schema_version": 2, "evidence": [], "entitlements": [], "entries": [],
        "payments": [{"payment_id": "pay_v", "transaction_reference": "U404",
                      "amount_minor": 200000, "candidate_id": cid,
                      "verification_state": "REJECTED",
                      "admin_disposition": "ADMIN_CONFIRMED_NOT_PAID"}],
    })
    record = find(recon.profile_rows(), "Voided But Balanced")
    assert record["classification"] == recon.EXACT_MATCH
    assert record["has_historical_void"] is True
    assert any("ADMIN_CONFIRMED_NOT_PAID" in note for note in record["notes"])


def test_a_historical_void_still_blocks_automatic_correction(env):
    cid = add("Voided And Short", "9000000117", payment=5000, expected=5000,
              proofs=[proof(6000, pid="p1", utr="U1")])
    pve._save_ledger({
        "schema_version": 2, "evidence": [], "entitlements": [], "entries": [],
        "payments": [{"payment_id": "pay_v", "transaction_reference": "U404",
                      "amount_minor": 100000, "candidate_id": cid,
                      "verification_state": "REJECTED",
                      "admin_disposition": "ADMIN_CONFIRMED_NOT_PAID"}],
    })
    record = find(recon.profile_rows(), "Voided And Short")
    assert record["classification"] == recon.SAFE_AUTOMATIC_CORRECTION
    assert record["auto_correctable"] is False, "a person still looks at this one"


def test_a_live_mismatch_is_not_hidden_behind_a_historical_void(env):
    cid = add("Voided And Mismatched", "9000000118", payment=20000,
              proofs=[proof(2000, pid="p1", utr="U1")])
    pve._save_ledger({
        "schema_version": 2, "evidence": [], "entitlements": [], "entries": [],
        "payments": [{"payment_id": "pay_v", "transaction_reference": "U404",
                      "amount_minor": 100000, "candidate_id": cid,
                      "verification_state": "REJECTED",
                      "admin_disposition": "ADMIN_CONFIRMED_NOT_PAID"}],
    })
    record = find(recon.profile_rows(), "Voided And Mismatched")
    assert record["classification"] == recon.GENUINE_MISMATCH
    assert record["has_historical_void"] is True


def test_notes_surface_duplicates_and_pending_review(env):
    add("Noted", "9000000119", payment=20000, proofs=[
        proof(20000, pid="p1", utr="U9"), proof(20000, pid="p2", utr="U9"),
        proof(5000, pid="p3", utr="U8", state="PENDING_MANUAL_REVIEW")])
    record = find(recon.profile_rows(), "Noted")
    assert any("more than once" in note for note in record["notes"])
    assert any("awaiting review" in note for note in record["notes"])
