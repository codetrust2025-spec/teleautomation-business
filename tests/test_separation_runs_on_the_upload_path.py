"""The sender/receiver separation has to run where uploads actually go.

The previous attempt put it inside `extract_payment_with_ollama`, thirteen
conditionals deep on a `backup_response` branch the ordinary upload never
reaches. Its test called the helper directly, passed, and production went on
rejecting Sowmya's receipt for the same reason as before -- a green test for a
path that never ran.

So these drive `verify_payment_screenshot`, the function the upload endpoint
calls, with the extractor stubbed to return the leaked payload. If the
separation is moved back off the convergence point, or guarded behind a branch,
these fail.
"""

from __future__ import annotations

import pytest

from features import payment_verification_engine as eng


LEAKED_EXTRACTION = {
    # What the model read from the real Google Pay receipt, sender leakage and
    # all: the payee's masked UPI, and the payer's own SBI account sitting in
    # the receiver account field.
    "is_payment_screenshot": True,
    "status": "completed",
    "payment_status": "completed",
    "amount": 5000,
    "confidence_score": 95,
    "receiver_name": "J RAVINDER",
    "receiver_upi_id": "••••1111@ybl",
    "receiver_account_identifier": "State Bank of India 4485",
    "receiver_account": "State Bank of India 4485",
    "receiver_phone_number": "",
    "receiver_phone": "",
    "sender_name": "LUKKA  PAVAN KALYAN",
    "sender_upi_id": "••••2761@okaxis",
    "sender_account_identifier": "State Bank of India 4485",
    "debited_from_identifier": "State Bank of India 4485",
    "transaction_id": "661139834383",
    "utr": "661139834383",
    "utr_number": "661139834383",
}


@pytest.fixture
def upload(monkeypatch):
    """Run verify_payment_screenshot with the extraction stubbed, nothing else.

    The registry is stubbed to the one company account this receipt pays --
    J Ravinder's raviarvind1111@ybl -- so the test states what it depends on
    instead of inheriting whatever the environment happens to have configured.
    """
    company = eng._receiver_record(
        "company-j-ravinder-upi", "company", "J Ravinder",
        upi_ids=["raviarvind1111@ybl"],
        aliases=["Jollu Ravinder", "J RAVINDER", "Interview support"],
        verification_status="VERIFIED", verified_by="test",
    )
    monkeypatch.setattr(eng, "receiver_registry", lambda **_kw: [company])

    def run(extraction: dict, *, expected_amount: int = 5000) -> dict:
        import features.ollama_payment_extract as extract_module

        monkeypatch.setattr(
            extract_module, "extract_payment_with_ollama",
            lambda *a, **k: dict(extraction),
        )
        return eng.verify_payment_screenshot(
            b"\xff\xd8\xff\xe0 fake jpeg bytes",
            "image/jpeg",
            source_module="candidate_payment_proof",
            expected_amount=expected_amount,
            entity_id="sowmya-test",
            entity_name="sowmya",
            purpose="candidate_payment",
            payment_scope="ROUND",
            create_ledger=False,
        )
    return run


class TestTheUploadPathAppliesTheSeparation:
    def test_the_payers_account_does_not_become_a_receiver_conflict(self, upload):
        """The live symptom: 'the receiver name resembles a registered account,
        but the visible payment identifier does not match it'."""
        result = upload(LEAKED_EXTRACTION)
        assert result["receiver_identifier_conflict"] is False

    def test_the_receiver_resolves_to_the_registered_company(self, upload):
        result = upload(LEAKED_EXTRACTION)
        assert result["receiver_type"] == "company"
        assert result["receiver_match"] == "masked_upi_alias"

    def test_the_payment_verifies(self, upload):
        result = upload(LEAKED_EXTRACTION)
        assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
        assert not result.get("reason_codes")

    def test_the_transaction_reference_survives(self, upload):
        result = upload(LEAKED_EXTRACTION)
        assert result.get("utr_number") == "661139834383"


class TestItStillRefusesWhatItShould:
    def test_a_receiver_account_matching_nothing_is_still_a_conflict(self, upload):
        """The separation removes values that echo the sender. A receiver
        account that is genuinely the receiver's and matches no registered
        account must still raise the conflict."""
        genuine_mismatch = {
            **LEAKED_EXTRACTION,
            "receiver_upi_id": "",
            "receiver_account": "9988",
            "receiver_account_identifier": "HDFC 9988",
        }
        result = upload(genuine_mismatch)
        assert result["receiver_identifier_conflict"] is True
        assert result["verification_state"] != "VERIFIED_COMPANY_PAYMENT"

    def test_an_unregistered_masked_handle_is_not_accepted(self, upload):
        result = upload({
            **LEAKED_EXTRACTION,
            "receiver_upi_id": "••••9999@ybl",
        })
        assert result["verification_state"] != "VERIFIED_COMPANY_PAYMENT"

    def test_a_failed_transaction_is_still_refused(self, upload):
        result = upload({**LEAKED_EXTRACTION, "status": "failed",
                         "payment_status": "failed"})
        assert result["verification_state"] != "VERIFIED_COMPANY_PAYMENT"


class TestTheSeparationIsNotBehindABranch:
    def test_it_is_called_from_verify_payment_screenshot_itself(self):
        """Not from a helper several conditionals deep. If it moves back into a
        branch, the call disappears from this function and this fails."""
        import inspect

        source = inspect.getsource(eng.verify_payment_screenshot)
        assert "_drop_sender_values_from_receiver_fields(normalized_extraction)" in source

    def test_it_runs_before_the_receiver_is_classified(self):
        import inspect

        source = inspect.getsource(eng.verify_payment_screenshot)
        assert source.index("_drop_sender_values_from_receiver_fields") < source.index(
            "classify_receiver("
        )
