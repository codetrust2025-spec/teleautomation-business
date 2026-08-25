"""A pending payment proof must be filed under the identity that will claim it.

/public/slots/payment-proof files a proof under a service type, and
/bookings/confirm only resolves a proof whose service type matches the booking.
An upload that omits it is filed under "profile_service", so a round-wise
booking could never claim its own payment: the upload answered 200, the
confirmation answered 400 "Upload and verify the payment screenshot to
continue.", and no amount of re-uploading changed it. The same omission also
measured the receipt against the profile-service balance instead of the round
fee, so a brand-new client was told ₹0 was due and that the payment was
complete before anything had been checked against ₹5,000.

The browser is the only caller of these endpoints, so these drive the exact
payloads it sends rather than a convenient shape.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending

PHONE = "9876543210"
ROUND_FEE = 5000


def _verified_payment() -> dict:
    return {
        "verification_engine": "central_payment_verification_v2",
        "booking_eligible": True,
        "company_payment_verified": True,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "is_payment_screenshot": True,
        "status": "success",
        "amount": ROUND_FEE,
        "amount_sufficient": True,
        "confidence_score": 99,
        "utr_number": "123456789012",
        "receiver_type": "company",
        "receiver_upi_id": "company@ybl",
        "deterministic_reasons": [],
    }


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr(
        "features.payment_verification_engine.verify_payment_screenshot",
        lambda *_args, **_kwargs: _verified_payment(),
    )
    monkeypatch.setattr(
        "features.ollama_payment_extract.generate_payment_narrative",
        lambda *_args, **_kwargs: "Verified payment",
    )
    monkeypatch.setattr(
        "features.payment_proof_validator.validate_interview_invite",
        lambda *_args, **_kwargs: (True, ""),
    )
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def _upload(client: TestClient, **identity):
    """The payment upload, as the submit-slot form posts it."""
    data = {"name": "Raju", "existing_proof_ids": ""}
    data.update(identity)
    return client.post(
        "/public/slots/payment-proof",
        data=data,
        files=[("files", ("payment.jpg", b"verified-payment", "image/jpeg"))],
    )


def _confirm(client: TestClient, proof_ids, *, phone: str = PHONE):
    """The booking confirmation, as the submit-slot form posts it."""
    return client.post(
        "/bookings/confirm",
        data={
            "name": "Raju",
            "service_type": "round_wise",
            "date": "2026-09-01",
            "time": "14:00",
            "time_end": "15:00",
            "interview_round": "L1",
            "technology": "ETL",
            "phone": phone,
            "candidate_id": "",
            "payment_proof_ids": ",".join(proof_ids),
            "idempotency_key": "raju|round_wise|2026-09-01",
        },
        files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
    )


def _round_wise_identity() -> dict:
    return {
        "service_type": "round_wise",
        "phone": PHONE,
        "candidate_id": "",
        "technology": "ETL",
        "interview_round": "L1",
    }


def test_round_wise_proof_carrying_its_identity_books_the_slot(monkeypatch, tmp_path):
    """The whole point: upload, then confirm, and the slot is actually booked."""
    client = _client(monkeypatch, tmp_path)
    upload = _upload(client, **_round_wise_identity())
    assert upload.status_code == 200, upload.text

    confirmed = _confirm(client, upload.json()["proof_ids"])
    assert confirmed.status_code == 200, confirmed.text

    rows = [
        row for row in cs.list_candidates(stage="all", month="all")
        if cs.candidate_has_confirmed_slot(row)
    ]
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-09-01"
    assert rows[0]["payment"] == ROUND_FEE


def test_round_wise_upload_is_measured_against_the_round_fee(monkeypatch, tmp_path):
    """₹5,000 is the yardstick, not whatever the profile balance happens to be.

    Without the service type the upload fell back to the profile balance, which
    is ₹0 for someone who has no profile row — so the form reported the payment
    complete before a single rupee had been checked against the fee.
    """
    client = _client(monkeypatch, tmp_path)
    identified = _upload(client, **_round_wise_identity()).json()
    assert identified["amount_due"] == ROUND_FEE
    assert identified["remaining_due"] == 0
    assert identified["payment_complete"] is True

    anonymous = _upload(client).json()
    assert anonymous["amount_due"] == 0, (
        "an upload with no service type still measures against the profile "
        "balance; this is the behaviour round-wise must never fall back to"
    )


def test_a_proof_filed_without_a_service_type_cannot_book_round_wise(monkeypatch, tmp_path):
    """The regression itself, pinned from the client's side.

    This is what the browser used to send. It has to keep failing, because the
    proof genuinely belongs to a different service; what must not come back is
    the form sending this shape.
    """
    client = _client(monkeypatch, tmp_path)
    upload = _upload(client)
    assert upload.status_code == 200, upload.text

    stored = list((pending._load().get("proofs") or {}).values())
    assert [entry["service_type"] for entry in stored] == ["profile_service"]

    confirmed = _confirm(client, upload.json()["proof_ids"])
    assert confirmed.status_code == 400
    assert confirmed.json()["message"] == (
        "Upload and verify the payment screenshot to continue."
    )
    assert not cs.list_candidates(stage="all", month="all")


def test_a_proof_is_not_claimable_from_a_different_phone(monkeypatch, tmp_path):
    """The identity binds. A proof filed under one phone is not another's."""
    client = _client(monkeypatch, tmp_path)
    upload = _upload(client, **_round_wise_identity())
    assert upload.status_code == 200, upload.text

    confirmed = _confirm(client, upload.json()["proof_ids"], phone="9000000001")
    assert confirmed.status_code == 400
    assert confirmed.json()["message"] == (
        "Upload and verify the payment screenshot to continue."
    )


def test_profile_service_proofs_are_unaffected(monkeypatch, tmp_path):
    """Naming the service explicitly resolves exactly as omitting it did."""
    client = _client(monkeypatch, tmp_path)
    cs.create_candidate({
        "name": "Raju", "phone": PHONE,
        "service_type": "profile_service", "interview_round": "L1",
    })
    omitted = _upload(client).json()
    named = _upload(client, service_type="profile_service", candidate_id="").json()
    assert omitted["amount_due"] == named["amount_due"]
    assert named["amount_due"] > 0, "the profile balance is still the yardstick here"


def test_candidates_endpoint_publishes_the_round_wise_fee(monkeypatch, tmp_path):
    """The form has no roster row to read the round fee from, so it is served.

    It stays a display value: /bookings/confirm re-derives the same figure and
    is the only thing that decides whether a booking is paid for.
    """
    client = _client(monkeypatch, tmp_path)
    payload = client.get("/public/slots/candidates").json()
    assert payload["round_wise_fee"] == cs.baseline_for_service("round_wise")
    assert payload["round_wise_fee"] == ROUND_FEE
