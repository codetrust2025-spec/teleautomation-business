import json

from features.company_payment_verification import (
    configured_company_account_numbers,
    configured_company_phone_numbers,
    configured_company_upi_ids,
    verify_company_payment,
)
from features.ollama_payment_extract import _extract_amount_from_text, _ocr_regex_extraction

import pytest


@pytest.fixture(autouse=True)
def _isolated_receiver_registry(monkeypatch, tmp_path):
    # Isolate from real seed data so tests never depend on production identifiers.
    registry_path = tmp_path / "payment_receiver_accounts.json"
    registry_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry_path))


def _receipt(**patch):
    receipt = {
        "is_payment_screenshot": True,
        "amount": 5000,
        "receiver_name": "Sample Receiver",
        "receiver_upi_id": "company@upi",
        "utr_number": "482681255068",
        "status": "success",
    }
    receipt.update(patch)
    return receipt


def test_company_payment_accepts_configured_company_upi():
    verdict = verify_company_payment(
        _receipt(), 5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True
    assert verdict["reasons"] == []


def test_official_company_payment_defaults_survive_config_loader_failure(monkeypatch):
    monkeypatch.delenv("COMPANY_PAYMENT_UPI_IDS", raising=False)
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE_NUMBERS", raising=False)

    assert configured_company_upi_ids() == {"company@upi"}
    assert configured_company_phone_numbers() == {"9000000001"}


def test_registered_referrer_payment_is_accepted(monkeypatch, tmp_path):
    # Self-contained dummy referrer + account registry (no real identifiers).
    referrers = tmp_path / "referrers.json"
    referrers.write_text(
        json.dumps(
            {
                "version": 1,
                "referrers": [
                    {"id": "referrer-sample", "name": "SAMPLE REFERRER",
                     "aliases": [], "is_active": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "acct-sample",
                        "owner_type": "REFERRER",
                        "referrer_id": "referrer-sample",
                        "account_holder_name": "SAMPLE REFERRER",
                        "upi_id": "referrer@upi",
                        "verification_status": "VERIFIED",
                        "is_active": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers))
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))

    verdict = verify_company_payment(
        _receipt(
            receiver_name="SAMPLE REFERRER",
            receiver_upi_id="referrer@upi",
        ),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True
    assert verdict["receiver_type"] == "referrer"
    assert verdict["reasons"] == []
    assert verdict["receiver_type"] == "referrer"
    assert verdict["reasons"] == []


def test_receipt_without_visible_payee_fails_closed():
    verdict = verify_company_payment(
        _receipt(receiver_upi_id=""),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is False
    assert "receiving UPI ID or phone number is not visible" in " ".join(verdict["reasons"])


def test_failed_or_partial_company_payment_is_rejected():
    verdict = verify_company_payment(
        _receipt(amount=3000, status="failed"),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is False
    reasons = " ".join(verdict["reasons"])
    assert "successful, completed" in reasons
    assert "full ₹5,000" in reasons


def test_compact_company_upi_success_without_utr_is_allowed():
    verdict = verify_company_payment(
        _receipt(utr_number=""),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True


def test_company_payment_phone_is_allowed_without_upi():
    verdict = verify_company_payment(
        _receipt(
            amount=16000,
            receiver_name="SAMPLE RECEIVER",
            receiver_upi_id="",
            receiver_phone="+919000000001",
        ),
        16000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True
    assert verdict["receiver_phone"] == "9000000001"


def test_stored_company_payment_accepts_configured_bank_account(monkeypatch):
    from features.company_payment_verification import stored_proof_is_verified_company_payment

    monkeypatch.setenv("COMPANY_PAYMENT_ACCOUNT_NUMBERS", "1234567896367")
    assert configured_company_account_numbers() == {"1234567896367"}
    assert stored_proof_is_verified_company_payment({
        "company_payment_verified": True,
        "receiver_account": "XXXXXX6367",
    })


def test_ocr_fast_path_extracts_company_upi_from_compact_receipt():
    result = _ocr_regex_extraction(
        "Transaction Successful\n₹15,000.00\nPaid to SAMPLE RECEIVER\n"
        "PhonePe • company@upi\n30 June 2026, 8:01pm"
    )

    assert result is not None
    assert result["receiver_upi_id"] == "company@upi"


def test_ocr_fast_path_extracts_company_payment_phone():
    result = _ocr_regex_extraction(
        "Transaction Successful\nPaid to SAMPLE RECEIVER\n+919000000001\n"
        "₹16,000\nUTR: 633424783763"
    )

    assert result is not None
    assert result["receiver_phone"] == "+919000000001"


def test_ocr_amount_survives_when_rupee_symbol_is_dropped():
    text = "Paid to ravindra job hunter\nUTR: 265087185302\n15,000\n15,000"

    assert _extract_amount_from_text(text) == 15000
    result = _ocr_regex_extraction(text)
    assert result is not None
    assert result["amount"] == 15000
