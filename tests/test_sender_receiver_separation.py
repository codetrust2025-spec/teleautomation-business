"""The payer's own account must never be read as the payee's.

Sowmya's Google Pay receipt names the payee as "To: J RAVINDER, PhonePe
••••1111@ybl" and, below it, the payer's funding account as "State Bank of
India 4485". The model put 4485 in `receiver_account_identifier`, and from there
it was matched as the receiver's account: it matched no registered receiver, and
because a stable identifier now sat beside a receiver NAME that did match, the
payment was reported as a receiver-identity conflict -- "the receiver name
resembles a registered account, but the visible payment identifier does not
match it" -- for a payment to an account that is registered.

Two things had to change, and neither loosens receiver validation. The sender's
values are dropped from the receiver fields, and a masked receiver UPI is
matched against the registry whatever else was extracted: an identifier that
matches no registered receiver is not a reason to ignore one that does.
"""

from __future__ import annotations

import pytest

from features import ollama_payment_extract as extract
from features import payment_verification_engine as eng


def sowmya_receipt(**overrides) -> dict:
    """The receipt as the model read it, sender leakage included."""
    row = {
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
    }
    row.update(overrides)
    return row


class TestTheSendersValuesLeaveTheReceiverFields:
    def test_the_payers_own_account_is_dropped(self):
        row = sowmya_receipt()
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["receiver_account"] == ""
        assert row["receiver_account_identifier"] == ""

    def test_the_payees_upi_is_kept(self):
        """Only the leaked value goes. The field the receipt actually labels as
        the payee's is the one receiver matching depends on."""
        row = sowmya_receipt()
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["receiver_upi_id"] == "••••1111@ybl"

    def test_the_sender_side_is_left_untouched(self):
        """Nothing is lost -- the receipt can still be audited in full."""
        row = sowmya_receipt()
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["sender_upi_id"] == "••••2761@okaxis"
        assert row["sender_account_identifier"] == "State Bank of India 4485"
        assert row["sender_name"] == "LUKKA  PAVAN KALYAN"

    def test_it_compares_on_digits_not_on_spelling(self):
        """"State Bank of India 4485" and "4485" are the same account however
        each was written."""
        row = sowmya_receipt(receiver_account="4485")
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["receiver_account"] == ""

    def test_a_genuine_receiver_account_survives(self):
        """The guard drops what echoes the sender, not receiver accounts in
        general."""
        row = sowmya_receipt(
            receiver_account="9988", receiver_account_identifier="HDFC 9988",
        )
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["receiver_account"] == "9988"

    def test_a_receiver_upi_that_echoes_the_sender_is_dropped(self):
        row = sowmya_receipt(receiver_upi_id="••••2761@okaxis")
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["receiver_upi_id"] == ""

    def test_a_receiver_phone_that_echoes_the_sender_is_dropped(self):
        row = sowmya_receipt(
            receiver_phone="9876543210", sender_phone_number="9876543210",
        )
        extract._drop_sender_values_from_receiver_fields(row)
        assert row["receiver_phone"] == ""


class TestAStrayIdentifierNoLongerVetoesTheReceiverUpi:
    def test_the_masked_branch_is_reached_whatever_else_was_extracted(self):
        """It used to require that no other identifier existed at all, so one
        leaked value silently disabled the only field the screenshot labels as
        the receiver's."""
        import inspect

        source = inspect.getsource(eng.classify_receiver)
        masked_branch = source[source.index("upi_masked"):]
        assert "not (upi or phone or account)" not in masked_branch.split("elif")[1]

    def test_a_conflict_is_reported_when_nothing_resolves(self):
        """The signal is not removed: a receiver identifier that matches no
        registered account, with no masked handle that does, is still a
        conflict."""
        conflict = eng.classify_receiver({
            "receiver_name": "J RAVINDER",
            "receiver_upi_id": "",
            "receiver_account": "4485",
            "receiver_phone_number": "",
        })
        assert conflict["receiver_identifier_conflict"] is True


class TestTheTransactionReferenceIsKept:
    @pytest.mark.parametrize("field", ["utr", "transaction_id"])
    def test_the_upi_transaction_id_survives_the_separation(self, field):
        """661139834383 is the reference the duplicate checks key on; the
        separation must not touch it."""
        row = sowmya_receipt()
        extract._drop_sender_values_from_receiver_fields(row)
        assert row[field] == "661139834383"
