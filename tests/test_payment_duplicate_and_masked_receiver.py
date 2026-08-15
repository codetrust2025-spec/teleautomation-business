"""Two defects that together left a visible payment proof crediting nothing.

A candidate could show an uploaded, readable screenshot on screen and still read
Received 0. Neither defect announced itself: the proof simply sat in
DUPLICATE_PAYMENT with no payment row anywhere in the ledger.
"""

import json

import pytest

from features import payment_verification_engine as engine


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, tmp_path):
    registry_path = tmp_path / "payment_receiver_accounts.json"
    registry_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv(
        "PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json")
    )


def _extraction(**patch):
    row = {
        "amount": 5000,
        "receiver_name": "SAMPLE RECEIVER",
        "receiver_upi_id": "company@upi",
        "receiver_phone": "",
        "receiver_account": "",
        "utr_number": "123456789012",
        "transaction_id": "",
        "reference_number": "",
        "payment_date": "2026-07-27",
        "payment_time": "11:40 AM",
        "status": "success",
        "confidence_score": 98,
        "is_payment_screenshot": True,
        "primary_model": "qwen3-vl:8b-instruct",
        "receiver_type": "company",
    }
    row.update(patch)
    return row


def _install_extractor(monkeypatch, value):
    def fake_extract(_raw, _mime, **kwargs):
        return dict(value)

    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        fake_extract,
    )


def _company(monkeypatch, names="J Ravinder,Jollu Ravinder", upi_ids=""):
    """Pin the company receiver so these tests never read ambient config."""
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", names)
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", upi_ids)


def _payments(tmp_path, entity_id):
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    return [
        row
        for row in ledger["payments"]
        if row.get("source_entity_id") == entity_id
    ]


# --- Defect 1: a non-crediting attempt latched the evidence to the wrong row ---


def test_rejected_upload_on_wrong_profile_does_not_block_the_real_candidate(
    monkeypatch, tmp_path
):
    """A screenshot first uploaded against the wrong profile used to own that
    evidence forever. The second, correct upload matched the earlier payment,
    saw a different entity, and returned DUPLICATE_PAYMENT after an early return
    that never creates a payment row -- so the candidate stayed at Received 0
    with the proof plainly visible. The first attempt credited nobody, so there
    is nothing to double-count and nothing to protect.
    """
    _company(monkeypatch)
    image = b"one-and-the-same-screenshot"

    _install_extractor(
        monkeypatch,
        _extraction(
            amount=0,
            status="unknown",
            utr_number="",
            confidence_score=0,
            is_payment_screenshot=False,
            receiver_name="",
            receiver_upi_id="",
        ),
    )
    first = engine.verify_payment_screenshot(
        image,
        source_module="candidate_payment_proof",
        entity_id="wrong-profile",
        entity_name="Summary Of Qualifications",
    )
    assert first["verification_state"] == "REJECTED"

    _install_extractor(monkeypatch, _extraction())
    second = engine.verify_payment_screenshot(
        image,
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_id="real-candidate",
        entity_name="Candidate",
    )

    assert second["verification_state"] != "DUPLICATE_PAYMENT"
    assert second["company_payment_verified"] is True

    owned = _payments(tmp_path, "real-candidate")
    assert len(owned) == 1
    assert owned[0]["amount_minor"] == 500000


def test_repeating_that_upload_stays_idempotent(monkeypatch, tmp_path):
    """Stepping over the rejected attempt must not spawn a fresh payment row on
    every re-verification of the same evidence for the same entity."""
    _company(monkeypatch)
    image = b"repeated-screenshot"

    _install_extractor(monkeypatch, _extraction(is_payment_screenshot=False, amount=0))
    engine.verify_payment_screenshot(
        image, source_module="candidate_payment_proof", entity_id="wrong-profile"
    )

    _install_extractor(monkeypatch, _extraction())
    for _ in range(3):
        engine.verify_payment_screenshot(
            image,
            source_module="candidate_payment_proof",
            expected_amount=5000,
            entity_id="real-candidate",
        )

    assert len(_payments(tmp_path, "real-candidate")) == 1


def test_a_credited_payment_still_blocks_reuse_on_another_candidate(
    monkeypatch, tmp_path
):
    """The protection that matters is untouched: one genuine receipt cannot be
    counted for two candidates."""
    _company(monkeypatch)
    image = b"genuine-receipt"
    _install_extractor(monkeypatch, _extraction())

    first = engine.verify_payment_screenshot(
        image,
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_id="candidate-a",
    )
    assert first["company_payment_verified"] is True

    second = engine.verify_payment_screenshot(
        image,
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_id="candidate-b",
    )
    assert second["verification_state"] == "DUPLICATE_PAYMENT"
    assert second["company_payment_verified"] is False
    assert not _payments(tmp_path, "candidate-b")


# --- Defect 2: a redaction mask was read as a bank account ---


def test_masked_payee_handle_is_not_treated_as_an_identifier(monkeypatch):
    """PhonePe prints the payee VPA as XXXXXX4573@ybl. The mask satisfies the
    valid-UPI pattern, so it was taken for a real account: it suppressed the
    name fallback and raised receiver_identifier_conflict, turning a payment to
    a registered company receiver into an unknown one."""
    _company(monkeypatch)

    receiver = engine.classify_receiver(
        {"receiver_name": "JOLLU RAVINDER", "receiver_upi_id": "XXXXXX4573@ybl"}
    )

    assert receiver["receiver_identifier_masked"] is True
    assert receiver["receiver_identifier_conflict"] is False
    assert receiver["receiver_type"] == "company"
    assert receiver["receiver_match"] == "name"


def test_a_real_handle_still_outranks_the_name(monkeypatch):
    """The mask rule must not loosen matching for handles that really are
    printed: an unmasked handle belonging to nobody in the registry stays a
    conflict rather than falling back to the name."""
    _company(monkeypatch, upi_ids="real@ybl")

    conflicting = engine.classify_receiver(
        {"receiver_name": "JOLLU RAVINDER", "receiver_upi_id": "someoneelse@ybl"}
    )
    assert conflicting["receiver_identifier_masked"] is False
    assert conflicting["receiver_identifier_conflict"] is True
    assert conflicting["receiver_type"] == "unknown"

    matching = engine.classify_receiver(
        {"receiver_name": "JOLLU RAVINDER", "receiver_upi_id": "real@ybl"}
    )
    assert matching["receiver_match"] == "upi"
    assert matching["receiver_match_score"] == 100


def test_masked_handle_with_an_unregistered_name_stays_unknown(monkeypatch):
    """Ignoring the mask must not let an unrecognised payee through."""
    _company(monkeypatch)

    receiver = engine.classify_receiver(
        {"receiver_name": "SOMEONE ELSE", "receiver_upi_id": "XXXXXX4573@ybl"}
    )
    assert receiver["receiver_type"] == "unknown"
    assert receiver["receiver_match"] == ""


def test_masked_company_payment_needs_review_rather_than_silent_zero(
    monkeypatch,
):
    """A mask still cannot stand in for a stable identifier, so the payment is
    not auto-credited. What changes is that it lands in a state that asks for
    review instead of vanishing as a duplicate."""
    _company(monkeypatch)
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=10000,
            receiver_name="JOLLU RAVINDER",
            receiver_upi_id="XXXXXX4573@ybl",
            utr_number="757988842904",
            transaction_id="T2608071210007608760634",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"masked-receipt",
        source_module="candidate_payment_proof",
        expected_amount=20000,
        entity_id="poojitha",
        referrer_hint="Ravinder",
    )

    assert result["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
    assert "STABLE_RECEIVER_IDENTIFIER_REQUIRED" in result["reason_codes"]
    assert result["company_payment_verified"] is False
