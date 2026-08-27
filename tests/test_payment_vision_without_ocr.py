"""Payment screenshots must read correctly with OCR switched off.

The architecture is screenshot -> Ollama vision -> structured parsing ->
validation. Tesseract is a cross-check, not a dependency, and
``OCR_ENABLED=false`` is a supported production state.

Production defect (2026-08-27): a PhonePe receipt showing Transaction ID
``T260519149403948648792``, UTR ``800409041280`` and ₹20,000 was rejected with
"A valid UTR or transaction ID is required." while OCR was disabled.

The parser was never at fault. Measured against the live models with that
screenshot:

    moondream             transaction_id ""   utr ""   amount 0     <- unusable
    qwen2.5vl:7b          all three correct
    qwen3-vl:8b-instruct  all three correct

Production had ``OLLAMA_VISION_MODEL=moondream``, which overrode the routing
default of ``qwen2.5vl:7b`` for every vision workload. So the vision path
returned empty identifiers and, with no OCR to fall back on, validation
correctly refused a proof it could not identify.

The existing PhonePe tests call the regex helpers directly, so they pass while
this path is broken. These drive the real entry point with OCR off and
Tesseract made unavailable, which is the arrangement production actually runs.
"""

from __future__ import annotations

import json

import pytest

from features import ollama_payment_extract as extractor
from features.payment_fraud_detection import (
    assess_payment_proof,
    payment_transaction_identities,
)

TXN_ID = "T260519149403948648792"
UTR = "800409041280"
AMOUNT = 20000

# What a capable vision model returns for that receipt, matching the schema in
# PAYMENT_EXTRACTION_PROMPT.
VISION_JSON = json.dumps({
    "payment_status": "SUCCESS",
    "direction": "PAID_TO",
    "amount_text": "₹20,000",
    "amount": AMOUNT,
    "visible_amounts": [AMOUNT, AMOUNT],
    "currency": "INR",
    "receiver_name": "J Ravinder",
    "receiver_upi_id": "raviarvind1111@ybl",
    "debited_from_identifier": "XXXXXXXXXXX0514",
    "transaction_id": TXN_ID,
    "utr": UTR,
    "transaction_date": "2026-05-19",
    "transaction_time": "11:49 AM",
    "provider": "PhonePe",
    "missing_fields": [],
    "warnings": [],
    "is_payment_screenshot": True,
})

IMAGE = b"\x89PNG\r\n\x1a\n" + b"phonepe-receipt-bytes"


@pytest.fixture
def ocr_off(monkeypatch):
    """OCR globally disabled, and Tesseract made to fail if anything calls it.

    Returns a list that records every Tesseract call, so a test can assert the
    vision path stood on its own rather than being rescued by OCR.
    """
    calls: list[bytes] = []

    def _no_tesseract(image_data):
        calls.append(image_data)
        raise AssertionError("Tesseract must not be required when OCR is off")

    monkeypatch.setattr(extractor, "ocr_enabled", lambda: False)
    monkeypatch.setattr(extractor, "_run_tesseract_ocr", _no_tesseract)
    monkeypatch.setattr(extractor, "_is_ollama_available", lambda: True)
    return calls


def _vision_returns(monkeypatch, payload: str, seen: list | None = None):
    def _fake(model_name, image_base64, prompt, *, timeout=0):
        if seen is not None:
            seen.append({"model": model_name, "prompt": prompt})
        return payload

    monkeypatch.setattr(extractor, "_call_vision_model", _fake)


def test_the_identifiers_are_extracted_with_ocr_off(monkeypatch, ocr_off):
    """The regression. Vision alone must yield both identifiers and the amount."""
    _vision_returns(monkeypatch, VISION_JSON)

    result = extractor.extract_payment_with_ollama(IMAGE) or {}

    assert result.get("transaction_id") == TXN_ID
    assert result.get("utr_number") == UTR
    assert result.get("amount") == AMOUNT
    assert ocr_off == [], "Tesseract was called; the vision path is not self-sufficient"


def test_validation_accepts_the_proof_with_ocr_off(monkeypatch, ocr_off):
    """End of the path: the exact error from the report must not appear."""
    _vision_returns(monkeypatch, VISION_JSON)
    result = extractor.extract_payment_with_ollama(IMAGE) or {}

    assert payment_transaction_identities(result) == {TXN_ID, UTR}

    monkeypatch.setattr(
        "features.candidate_store._load", lambda: {"candidates": []}
    )
    assessment = assess_payment_proof(IMAGE, result, candidate_name="probe")
    assert "A valid UTR or transaction ID is required." not in assessment["reasons"]


def test_a_model_that_returns_blank_identifiers_is_not_accepted(monkeypatch, ocr_off):
    """What moondream actually did. Refusing is correct - the guard must hold.

    This is the half that must NOT be weakened: an unreadable proof has to be
    rejected, not waved through, because the duplicate check keys on exactly
    these identifiers.
    """
    _vision_returns(monkeypatch, json.dumps({
        "payment_status": "SUCCESS", "amount": 0,
        "transaction_id": "", "utr": "", "is_payment_screenshot": True,
    }))

    result = extractor.extract_payment_with_ollama(IMAGE) or {}

    assert payment_transaction_identities(result) == set()
    monkeypatch.setattr("features.candidate_store._load", lambda: {"candidates": []})
    assessment = assess_payment_proof(IMAGE, result, candidate_name="probe")
    assert "A valid UTR or transaction ID is required." in assessment["reasons"]


def test_a_truncated_identifier_is_not_silently_accepted(monkeypatch, ocr_off):
    """A shortened ID is worse than none: it would key the duplicate check on a
    value that does not match the real payment."""
    _vision_returns(monkeypatch, json.dumps({
        "payment_status": "SUCCESS", "amount": AMOUNT,
        "transaction_id": "T2605191494", "utr": "", "is_payment_screenshot": True,
    }))

    result = extractor.extract_payment_with_ollama(IMAGE) or {}
    assert result.get("transaction_id") != TXN_ID, (
        "a truncated id must not be mistaken for the real one"
    )


def test_the_prompt_tells_the_model_to_copy_identifiers_exactly(monkeypatch, ocr_off):
    """Long digit strings are exactly what a vision model paraphrases. The
    prompt has to forbid it, or a capable model still returns a tidy-looking
    but wrong value."""
    seen: list = []
    _vision_returns(monkeypatch, VISION_JSON, seen)
    extractor.extract_payment_with_ollama(IMAGE)

    assert seen, "vision model was never called"
    prompt = seen[0]["prompt"]
    assert "DIGIT FOR DIGIT" in prompt
    for word in ("shorten", "truncate", "summarise"):
        assert word in prompt, f"prompt does not forbid {word!r}"


def test_the_backup_vision_model_can_also_read_identifiers():
    """The backup runs when the primary fails, so it must be able to do the
    same job. moondream was the old default and returns empty identifiers,
    which made a failed fallback look like an unreadable receipt."""
    assert extractor.OLLAMA_BACKUP_VISION_MODEL != "moondream"


def test_payment_vision_routes_to_a_capable_model_by_default():
    """The routing default must not be a model that cannot read the fields the
    authorisation depends on."""
    from core.ai_model_routing import AUXILIARY_MODEL_ROUTES

    _, default = AUXILIARY_MODEL_ROUTES["payment_screenshot_vision"]
    assert default != "moondream"
    assert "vl" in default.lower(), "payment vision needs a vision-language model"
