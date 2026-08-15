"""Received total inside the candidate store: derivation, manual override, audit."""
import pytest

from features import candidate_store, payment_recalculation_audit


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setenv(
        "PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc_audit.json")
    )
    # The store keeps a short-lived module-level read cache; without clearing it
    # each test would see the previous test's roster.
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def verified(amount, *, pid, utr):
    return {
        "id": pid,
        "attachment_type": "payment_proof",
        "verified_amount": amount,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "utr_number": utr,
        "transaction_id": utr,
        "filename": f"{pid}.png",
    }


def make(store, *, name, payment, expected, proofs=(), proof_controlled=None):
    """Build a candidate row directly.

    `proof_controlled` mirrors what the real upload path does: the first proof
    that goes through `add_payment_proof` marks the row as proof-controlled, so
    later reductions apply without a reconciliation prompt. It defaults to True
    whenever proofs are supplied.
    """
    row = store.create_candidate({
        "name": name, "phone": "9000000001",
        "expected_payment": expected, "payment": payment,
    })
    data = store._load()
    for item in data["candidates"]:
        if item["id"] == row["id"]:
            item["payment_proofs"] = [dict(p) for p in proofs]
            item["payment"] = payment
            item["payment_proof_controlled"] = (
                bool(proofs) if proof_controlled is None else proof_controlled
            )
    store._save(data)
    return row["id"]


def test_received_reads_the_proof_total_not_the_typed_value(store):
    cid = make(store, name="Above Minimum", payment=5000, expected=5000,
               proofs=[verified(7000, pid="p1", utr="U1")])
    row = store.get_candidate(cid)
    assert row["payment"] == 7000
    assert row["verified_received"] == 7000
    assert row["above_minimum"] == 2000
    assert row["balance_due"] == 0
    assert row["payment_status"] == "paid"
    assert row["payment_is_proof_derived"] is True


def test_two_proofs_sum_and_exceed_expected(store):
    cid = make(store, name="Two Proofs", payment=5000, expected=5000, proofs=[
        verified(5000, pid="p1", utr="U1"), verified(7000, pid="p2", utr="U2"),
    ])
    row = store.get_candidate(cid)
    assert row["payment"] == 12000
    assert row["verified_proof_count"] == 2
    assert row["above_minimum"] == 7000


def test_manual_edit_cannot_override_a_proof_derived_total(store):
    cid = make(store, name="Manual Override", payment=6000, expected=5000,
               proofs=[verified(6000, pid="p1", utr="U1")])
    store.update_candidate(cid, {"payment": 99000})
    row = store.get_candidate(cid)
    assert row["payment"] == 6000, "typed amount must not beat the proofs"


def test_manual_edit_still_works_when_there_is_no_proof(store):
    cid = make(store, name="No Proof", payment=0, expected=20000)
    store.update_candidate(cid, {"payment": 20000})
    row = store.get_candidate(cid)
    assert row["payment"] == 20000
    assert row["payment_is_proof_derived"] is False
    assert row["payment_unevidenced"] is True


def test_row_without_proof_keeps_its_recorded_amount(store):
    cid = make(store, name="Legacy Paid", payment=20000, expected=20000)
    row = store.get_candidate(cid)
    assert row["payment"] == 20000
    assert row["balance_due"] == 0


def test_recalculation_after_rejecting_a_proof_writes_an_audit_entry(store):
    cid = make(store, name="Rejected Later", payment=12000, expected=5000, proofs=[
        verified(5000, pid="p1", utr="U1"), verified(7000, pid="p2", utr="U2"),
    ])
    assert store.get_candidate(cid)["payment"] == 12000

    data = store._load()
    for item in data["candidates"]:
        if item["id"] == cid:
            item["payment_proofs"][0]["verification_state"] = "REJECTED"
    store._save(data)

    store.recalculate_received_total(
        cid, trigger="proof_rejected", proof_change="rejected", proof_id="p1",
        reason="Reviewer rejected the ₹5,000 proof.", reviewer="admin",
    )
    assert store.get_candidate(cid)["payment"] == 7000

    entries = payment_recalculation_audit.entries(candidate_id=cid)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["previous_received_total"] == 12000
    assert entry["new_received_total"] == 7000
    assert entry["delta"] == -5000
    assert entry["trigger"] == "proof_rejected"
    assert entry["reviewer"] == "admin"
    assert entry["reason"]


def test_audit_log_is_append_only(store):
    cid = make(store, name="Append Only", payment=5000, expected=5000,
               proofs=[verified(5000, pid="p1", utr="U1")])
    data = store._load()
    for item in data["candidates"]:
        if item["id"] == cid:
            item["payment_proofs"].append(verified(7000, pid="p2", utr="U2"))
    store._save(data)
    store.recalculate_received_total(
        cid, trigger="proof_added", reason="second proof", proof_id="p2")

    data = store._load()
    for item in data["candidates"]:
        if item["id"] == cid:
            item["payment_proofs"] = [item["payment_proofs"][0]]
    store._save(data)
    store.recalculate_received_total(
        cid, trigger="proof_deleted", reason="second proof removed", proof_id="p2")

    entries = payment_recalculation_audit.entries(candidate_id=cid)
    assert [(e["previous_received_total"], e["new_received_total"]) for e in entries] == [
        (5000, 12000), (12000, 5000),
    ]


def test_deleting_a_proof_recalculates_through_the_public_api(store):
    cid = make(store, name="Delete Path", payment=12000, expected=5000, proofs=[
        verified(5000, pid="p1", utr="U1"), verified(7000, pid="p2", utr="U2"),
    ])
    assert store.get_candidate(cid)["payment"] == 12000
    assert store.delete_proof(cid, "p1") is True
    assert store.get_candidate(cid)["payment"] == 7000


def test_slot_clone_rows_do_not_multiply_one_payment(store):
    """A profile's slot clones each carry a copy of the same proof."""
    shared = verified(20000, pid="shared", utr="T123")
    first = make(store, name="Cloned", payment=20000, expected=20000, proofs=[shared])
    row = store.get_candidate(first)
    assert row["payment"] == 20000
    assert row["verified_proof_count"] == 1


def test_shortfall_on_a_row_never_under_proof_control_is_not_applied(store):
    """A ₹2,000 receipt against ₹20,000 recorded means proof capture is
    incomplete, so the recorded amount stands and reconciliation is flagged."""
    cid = make(store, name="Partial Evidence", payment=20000, expected=20000,
               proofs=[verified(2000, pid="p1", utr="U1")], proof_controlled=False)
    row = store.get_candidate(cid)
    assert row["payment"] == 20000
    assert row["payment_needs_reconciliation"] is True
    assert row["payment_reconciliation_gap"] == 18000
    assert row["verified_proof_total"] == 2000

    assert store.recalculate_received_total(
        cid, trigger="proof_added", reason="partial receipt") is None
    assert store.get_candidate(cid)["payment"] == 20000


def test_legacy_proof_without_state_leaves_the_recorded_amount_alone(store):
    cid = make(store, name="Legacy Upload", payment=50000, expected=50000,
               proofs=[{"id": "old", "filename": "old.png"}], proof_controlled=False)
    row = store.get_candidate(cid)
    assert row["payment"] == 50000
    assert row["payment_is_proof_derived"] is False
    assert row["payment_unevidenced"] is True


def test_proof_control_survives_an_unrelated_edit(store):
    cid = make(store, name="Keeps Flag", payment=6000, expected=5000,
               proofs=[verified(6000, pid="p1", utr="U1")])
    store.update_candidate(cid, {"notes": "touched"})
    data = store._load()
    row = next(item for item in data["candidates"] if item["id"] == cid)
    assert row["payment_proof_controlled"] is True
    assert store.get_candidate(cid)["payment"] == 6000
