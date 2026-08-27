"""Regression tests for PhonePe-style Transaction ID and 13-digit UTR extraction.

Production defect: a PhonePe screenshot was rejected with "A valid UTR or
transaction ID is required." because:
  1. The standalone digit regex only matched exactly 12 digits (missed 13-digit UTR)
  2. No dedicated extractor for T-prefix PhonePe Transaction IDs
  3. The OCR cross-check after vision never backfilled transaction_id

The identifiers used below are SYNTHETIC, not the values from that receipt.
``8004909041280`` is 13 digits on purpose: it is what exercises fix (1), and
replacing it with a real 12-digit UTR would silently retire the regression this
file exists for. The actual receipt showed UTR ``800409041280`` (12 digits) and
Transaction ID ``T2605191149403948648792`` (23 chars, note the ``11``) — see
tests/test_payment_vision_without_ocr.py, which asserts the real ones.

These tests call the regex helpers directly, so they pass whether or not the
gated path that production actually runs is working. That is precisely how this
defect stayed hidden; treat a green run here as saying nothing about production.
"""

import json

import pytest

from features import ollama_payment_extract as extractor
from features.payment_fraud_detection import payment_transaction_identities


# ── Sample OCR text matching real PhonePe receipt layout ─────────────────────

PHONEPE_OCR = """Payment Successful
May 26 at 2:49 PM

Paid to
SAMPLE RECEIVER
company@ybl

₹20,000

Transaction ID
T260519149403948648792

UTR: 8004909041280

PhonePe
"""

PHONEPE_OCR_MULTILINE = """Payment Successful
₹20,000.00
Transaction ID
T260519149403948648792
UTR
8004909041280
PhonePe
"""

PHONEPE_OCR_INLINE = """Payment Successful ₹20,000 Transaction ID: T260519149403948648792 UTR: 8004909041280 PhonePe"""


# ── _extract_utr_from_text ──────────────────────────────────────────────────

class TestExtractUtrFromText:
    def test_13_digit_utr(self):
        assert extractor._extract_utr_from_text("UTR: 8004909041280") == "8004909041280"

    def test_13_digit_standalone(self):
        """Standalone 13-digit number should now match (was only 12 before)."""
        text = "Payment done\n8004909041280\nPhonePe"
        assert extractor._extract_utr_from_text(text) == "8004909041280"

    def test_12_digit_still_works(self):
        assert extractor._extract_utr_from_text("some text 615527427709 end") == "615527427709"

    def test_14_digit_utr(self):
        assert extractor._extract_utr_from_text("paid 80049090412801 done") == "80049090412801"

    def test_16_digit_utr(self):
        assert extractor._extract_utr_from_text("ref 8004909041280123 ok") == "8004909041280123"

    def test_labeled_utr_takes_priority(self):
        text = "UTR: 8004909041280\nsome other 123456789012"
        assert extractor._extract_utr_from_text(text) == "8004909041280"

    def test_from_full_phonepe_ocr(self):
        result = extractor._extract_utr_from_text(PHONEPE_OCR)
        assert result == "8004909041280"


# ── _extract_transaction_id_from_text ───────────────────────────────────────

class TestExtractTransactionIdFromText:
    def test_t_prefix_phonepe_id(self):
        assert extractor._extract_transaction_id_from_text(
            "Transaction ID T260519149403948648792"
        ) == "T260519149403948648792"

    def test_t_prefix_standalone(self):
        """T-prefix ID should be found even without a label."""
        text = "Payment done\nT260519149403948648792\nPhonePe"
        assert extractor._extract_transaction_id_from_text(text) == "T260519149403948648792"

    def test_multiline_label_and_value(self):
        """Transaction ID label on one line, value on the next."""
        text = "Transaction ID\nT260519149403948648792"
        assert extractor._extract_transaction_id_from_text(text) == "T260519149403948648792"

    def test_colon_separated(self):
        text = "Transaction ID: T260519149403948648792"
        assert extractor._extract_transaction_id_from_text(text) == "T260519149403948648792"

    def test_does_not_match_short_ids(self):
        """IDs shorter than 15 digits after T should not match the T-prefix pattern."""
        assert extractor._extract_transaction_id_from_text("T12345") == ""

    def test_from_full_phonepe_ocr(self):
        result = extractor._extract_transaction_id_from_text(PHONEPE_OCR)
        assert result == "T260519149403948648792"

    def test_from_multiline_ocr(self):
        result = extractor._extract_transaction_id_from_text(PHONEPE_OCR_MULTILINE)
        assert result == "T260519149403948648792"


# ── _ocr_regex_extraction (fast path) ───────────────────────────────────────

class TestOcrRegexExtractionPhonePe:
    def test_phonepe_receipt_extracts_transaction_id_and_utr(self):
        result = extractor._ocr_regex_extraction(PHONEPE_OCR)
        assert result is not None
        assert result["amount"] == 20000
        assert result["utr_number"] == "8004909041280"
        assert result["transaction_id"] == "T260519149403948648792"
        assert result["is_payment_screenshot"] is True
        assert result["payment_app"] == "PhonePe"

    def test_fast_path_accepts_transaction_id_without_utr(self):
        """If only Transaction ID visible (no UTR), fast path should still return."""
        text = "Payment Successful\n₹15,000\nTransaction ID: T260519149403948648792\nPhonePe"
        result = extractor._ocr_regex_extraction(text)
        assert result is not None
        assert result["transaction_id"] == "T260519149403948648792"
        assert result["amount"] == 15000

    def test_inline_format(self):
        result = extractor._ocr_regex_extraction(PHONEPE_OCR_INLINE)
        assert result is not None
        assert result["utr_number"] == "8004909041280"
        assert result["transaction_id"] == "T260519149403948648792"
        assert result["amount"] == 20000


# ── Integration: fraud detection accepts the extracted identities ────────────

class TestFraudDetectionAcceptsExtractedIds:
    def test_transaction_id_passes_identity_check(self):
        extraction = {"utr_number": "", "transaction_id": "T260519149403948648792", "reference_number": ""}
        ids = payment_transaction_identities(extraction)
        assert len(ids) >= 1
        assert "T260519149403948648792" in ids

    def test_13_digit_utr_passes_identity_check(self):
        extraction = {"utr_number": "8004909041280", "transaction_id": "", "reference_number": ""}
        ids = payment_transaction_identities(extraction)
        assert len(ids) >= 1
        assert "8004909041280" in ids

    def test_both_present(self):
        extraction = {
            "utr_number": "8004909041280",
            "transaction_id": "T260519149403948648792",
            "reference_number": "",
        }
        ids = payment_transaction_identities(extraction)
        assert len(ids) == 2

    def test_empty_fields_rejected(self):
        extraction = {"utr_number": "", "transaction_id": "", "reference_number": ""}
        ids = payment_transaction_identities(extraction)
        assert len(ids) == 0
