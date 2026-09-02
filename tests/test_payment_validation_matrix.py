"""Receiver validation, stated as the matrix it has to satisfy.

Every payload is the one production actually logged for the real Sowmya
receipt, read from the pending-payment index on the live host:

    receiver_name='J RAVINDER' receiver_upi='.....1111@ybl' receiver_account=''
    sender_upi='.....2761@okaxis' sender_account='State Bank of India 4485'
    amount=5000 status='SUCCESS' txn='661139834383'

The point of collecting them here is that accepting the genuine receipt and
refusing everything adjacent to it are the same decision seen from two sides.
Loosening the mask handling enough to accept a payment that was not made would
pass any test that only checks the happy path, so the refusals are stated
beside the acceptance rather than in a separate file.
"""

from __future__ import annotations

import pytest

from features import payment_verification_engine as eng
from features.ollama_payment_extract import _drop_sender_values_from_receiver_fields
from features.payment_fraud_detection import assess_payment_proof


REGISTERED_UPI = "raviarvind1111@ybl"
PRODUCTION_RECEIVER_UPI = ".....1111@ybl"
PRODUCTION_SENDER_UPI = ".....2761@okaxis"
SENDER_BANK_ACCOUNT = "State Bank of India 4485"
TRANSACTION_ID = "661139834383"


@pytest.fixture
def registry(monkeypatch):
    """One registered company account: J Ravinder's, exactly as configured."""
    company = eng._receiver_record(
        "company-j-ravinder-upi", "company", "J Ravinder",
        upi_ids=[REGISTERED_UPI],
        aliases=["Jollu Ravinder", "J RAVINDER"],
        verification_status="VERIFIED", verified_by="test",
    )
    monkeypatch.setattr(eng, "receiver_registry", lambda **_kw: [company])


def classify(**overrides) -> dict:
    payload = {
        "receiver_name": "J RAVINDER",
        "receiver_upi_id": PRODUCTION_RECEIVER_UPI,
        "receiver_account": "",
        "receiver_phone_number": "",
    }
    payload.update(overrides)
    return eng.classify_receiver(payload)


class TestWhatMustBeAccepted:
    def test_10_a_registered_company_receiver_is_accepted(self, registry):
        """The unmasked handle, matched outright."""
        result = classify(receiver_upi_id=REGISTERED_UPI)
        assert result["receiver_type"] == "company"
        assert result["receiver_identifier_conflict"] is False

    def test_11_the_dotted_mask_from_production_is_accepted(self, registry):
        result = classify()
        assert result["receiver_type"] == "company"
        assert result["receiver_match"] == "masked_upi_alias"
        assert result["receiver_match_score"] == 100
        assert result["receiver_identifier_conflict"] is False


class TestTheSenderIsNeverTheReceiver:
    """Both halves of the receipt name a bank and a handle. Reading the payer's
    as the payee's is what refused a genuine payment for eleven days."""

    def test_12_the_senders_sbi_4485_is_not_used_as_the_receiver_account(self):
        extraction = {
            "receiver_name": "J RAVINDER",
            "receiver_upi_id": PRODUCTION_RECEIVER_UPI,
            "receiver_account": SENDER_BANK_ACCOUNT,
            "receiver_account_identifier": SENDER_BANK_ACCOUNT,
            "sender_account_identifier": SENDER_BANK_ACCOUNT,
            "sender_upi_id": PRODUCTION_SENDER_UPI,
        }
        _drop_sender_values_from_receiver_fields(extraction)
        assert not extraction.get("receiver_account")
        assert not extraction.get("receiver_account_identifier")
        # The sender's own fields are untouched: this separates, it does not
        # erase.
        assert extraction["sender_account_identifier"] == SENDER_BANK_ACCOUNT
        assert extraction["sender_upi_id"] == PRODUCTION_SENDER_UPI

    def test_13_the_senders_okaxis_handle_never_matches_the_company(self, registry):
        """It must never be *credited as* the payee's identifier.

        Asserting `receiver_type != company` here would be testing the wrong
        thing: the payee name on this receipt genuinely is the company's, and
        with no @okaxis handle registered there is nothing for the mask to
        contradict, so the name still stands on its own. What must not happen
        is the payer's handle resolving as the payee's account -- and on the
        real path it is removed before classification ever sees it, which is
        what the next test and the separation suite check.
        """
        assert eng._masked_upi_alias_match(PRODUCTION_SENDER_UPI, [REGISTERED_UPI]) == ""
        result = classify(receiver_upi_id=PRODUCTION_SENDER_UPI)
        assert result["receiver_match"] != "masked_upi_alias"
        assert result["receiver_registry_id"] != "company-j-ravinder-upi" or (
            result["receiver_match"] == "name"
        )

    def test_the_senders_handle_echoed_into_the_receiver_field_is_dropped(self):
        extraction = {
            "receiver_upi_id": PRODUCTION_SENDER_UPI,
            "sender_upi_id": PRODUCTION_SENDER_UPI,
        }
        _drop_sender_values_from_receiver_fields(extraction)
        assert not extraction.get("receiver_upi_id")


class TestWhatMustBeRefused:
    def test_14_an_unknown_receiver_is_refused(self, registry):
        result = classify(receiver_name="SOMEONE ELSE", receiver_upi_id="stranger@ybl")
        assert result["receiver_type"] != "company"

    def test_15_a_mask_with_the_wrong_suffix_is_refused(self, registry):
        """Recognising dots as redaction must not accept every dotted handle."""
        result = classify(receiver_upi_id=".....9999@ybl")
        assert result["receiver_match"] != "masked_upi_alias"
        assert result["receiver_type"] != "company"

    def test_15b_a_mask_on_the_wrong_domain_is_refused(self, registry):
        """The visible digits match; the bank does not."""
        result = classify(receiver_upi_id=".....1111@okaxis")
        assert result["receiver_match"] != "masked_upi_alias"

    def test_16_the_wrong_receiver_name_is_refused(self, registry):
        """A mask only resolves for a name already on the account. Without that,
        four visible digits would be the whole security boundary."""
        result = classify(receiver_name="NOT RAVINDER")
        assert result["receiver_match"] != "masked_upi_alias"
        assert result["receiver_type"] != "company"

    def test_an_unresolvable_mask_does_not_fall_back_to_the_name(self, registry):
        """The regression that adding dotted-mask support opened.

        Masked handles are dropped from identifier matching so that a redacted
        receipt reads as one showing no handle, rather than one that disagrees
        with the registry. Once masks began resolving, that made an
        *unresolvable* mask indistinguishable from no identifier at all -- and
        the name-only branch below it then authorised the payment at score 90.
        A screenshot naming a registered payee while paying an account that is
        not theirs would have been credited to the company.

        A mask that resolves still scores 100 through masked_upi_alias; one
        that does not must leave the receiver unidentified.
        """
        result = classify(receiver_upi_id=".....9999@ybl")
        assert result["receiver_match"] != "name"
        assert result["receiver_identifier_conflict"] is True

    def test_a_receipt_showing_no_identifier_still_matches_on_name(self, registry):
        """The counterpart: the name-only path itself is not removed, only its
        reachability when a mask is present."""
        result = classify(receiver_upi_id="")
        assert result["receiver_match"] == "name"

    @pytest.mark.parametrize("status", ["failed", "failure", "declined", "reversed"])
    def test_17_a_failed_transaction_is_refused(self, status):
        assessment = assess_payment_proof(
            b"receipt",
            {"status": status, "transaction_id": TRANSACTION_ID, "amount": 5000},
            candidate_name="Sowmya",
        )
        assert assessment["decision"] == "rejected"
        assert any(status in reason for reason in assessment["reasons"])

    def test_17b_a_receipt_with_no_transaction_reference_is_refused(self):
        assessment = assess_payment_proof(
            b"receipt", {"status": "success", "amount": 5000}, candidate_name="Sowmya"
        )
        assert assessment["decision"] == "rejected"
        assert any("UTR" in reason for reason in assessment["reasons"])


class TestAmountAndReferenceStaySharp:
    def test_the_transaction_reference_survives_verification(self):
        from features.payment_fraud_detection import payment_transaction_identities

        assert payment_transaction_identities(
            {"transaction_id": TRANSACTION_ID}
        ) == {TRANSACTION_ID}

    def test_a_short_reference_is_not_accepted_as_one(self):
        """Eight characters minimum: a two-digit fragment would collide with
        every other receipt in the store."""
        from features.payment_fraud_detection import payment_transaction_identities

        assert payment_transaction_identities({"transaction_id": "1234"}) == set()
