"""One receiver registered twice must not read as two rival receivers.

Production defect (2026-08-27). A genuine ₹20,000 company payment to
``raviarvind1111@ybl`` stayed at *Needs review* — Verified proofs 0, Received
₹0, Outstanding ₹20,000 — even though extraction was perfect: the stored proof
held the right amount, the right transaction id and the right receiver UPI.

The UPI was registered twice:

    id=company                 verified_by=company_configuration  (COMPANY_PAYMENT_UPI_IDS)
    id=company-j-ravinder-upi  verified_by=admin                  (receiver registry file)

Both COMPANY, both active, both VERIFIED, both crediting the same payee.
``classify_receiver`` scores each identifier match at 100, so both tied for best
and ``len(best_matches) != 1`` sent it down the ambiguous branch:
``receiver_type: "unknown"`` -> not a company payment -> PENDING_MANUAL_REVIEW.

The guard itself is right: when one identifier is claimed by *different* payees
the engine must never pick one and credit money on a guess. It was only wrong
about what counts as a disagreement. Two records that would credit identically
are not a disagreement, they are the same answer written twice.

So the collapse is deliberately narrow. It requires the candidates to agree on
everything that changes the outcome — payee type, referrer, active window and
verification status — which means collapsing cannot alter a decision, only
avoid refusing to make one. Anything that could credit differently stays
ambiguous, and those cases are asserted below alongside the fix.
"""

from __future__ import annotations

import json

import pytest

from features import payment_verification_engine as engine

COMPANY_UPI = "raviarvind1111@ybl"


def _write_registry(tmp_path, accounts: list[dict]) -> str:
    path = tmp_path / "payment_receiver_accounts.json"
    path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return str(path)


def _company_account(**patch) -> dict:
    """An admin-registered COMPANY row carrying the same UPI as the config."""
    row = {
        "id": "company-j-ravinder-upi",
        "owner_type": "COMPANY",
        "account_holder_name": "J Ravinder",
        "upi_ids": [COMPANY_UPI],
        "aliases": ["j ravinder", "jollu ravinder"],
        "company_id": "company-teleautomation",
        "verification_status": "VERIFIED",
        "is_active": True,
    }
    row.update(patch)
    return row


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """Reproduce production: the company UPI in config *and* in the registry."""

    def _install(accounts: list[dict]):
        monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", COMPANY_UPI)
        monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", "J Ravinder")
        monkeypatch.setenv("PAYMENT_REFERRER_RECEIVERS_JSON", "")
        monkeypatch.setenv(
            "PAYMENT_RECEIVER_REGISTRY_FILE", _write_registry(tmp_path, accounts)
        )

    return _install


def _extraction(**patch) -> dict:
    row = {
        "receiver_upi_id": COMPANY_UPI,
        "receiver_name": "J Ravinder",
        "receiver_account": "",
        "receiver_phone": "",
        "amount": 20000,
        "transaction_id": "T2605191149403948648792",
        "utr_number": "800409041280",
        "payment_status": "success",
    }
    row.update(patch)
    return row


def test_the_duplicate_registration_is_reported_as_a_conflict(registry):
    """Groundwork: the engine already knows the registry is duplicated."""
    registry([_company_account()])

    conflicts = engine.receiver_registry_conflicts()

    assert any(c["identifier"] == COMPANY_UPI for c in conflicts), (
        "the duplicate must be visible to operators, not silent"
    )


def test_a_company_paid_twice_registered_still_resolves_to_the_company(registry):
    """The regression. This is the exact production shape."""
    registry([_company_account()])

    result = engine.classify_receiver(_extraction())

    assert result["receiver_type"] == "company", (
        "a genuine company payment was refused because the payee was "
        "registered twice; that is a registry hygiene problem, not a reason "
        "to withhold credit"
    )
    assert result["receiver_match_ambiguous"] is False
    assert result["receiver_match"] == "upi"


def test_the_collapse_is_recorded_so_the_registry_can_be_cleaned(registry):
    """Collapsing must not hide the duplicate — an operator has to be able to
    find and remove it."""
    registry([_company_account()])

    result = engine.classify_receiver(_extraction())

    assert sorted(result.get("receiver_match_duplicates") or []) == [
        "company",
        "company-j-ravinder-upi",
    ]


def test_a_upi_claimed_by_a_company_and_a_referrer_stays_ambiguous(registry):
    """The case the guard exists for. Company or referrer decides who is paid
    commission, so the engine must refuse rather than guess."""
    registry([
        _company_account(
            id="referrer-someone",
            owner_type="REFERRER",
            account_holder_name="Someone Else",
            company_id="",
        )
    ])

    result = engine.classify_receiver(_extraction())

    assert result["receiver_type"] == "unknown"
    assert result["receiver_match_ambiguous"] is True


def test_two_different_referrers_claiming_one_upi_stay_ambiguous(registry):
    """Same identifier, two people owed commission. Never auto-pick."""
    registry([
        _company_account(
            id="referrer-a", owner_type="REFERRER",
            account_holder_name="Referrer A", company_id="",
        ),
        _company_account(
            id="referrer-b", owner_type="REFERRER",
            account_holder_name="Referrer B", company_id="",
        ),
    ])
    # Drop the config company record so only the two referrers match.
    result = engine.classify_receiver(_extraction(receiver_name="Referrer A"))

    assert result["receiver_match_ambiguous"] is True
    assert result["receiver_type"] == "unknown"


def test_duplicates_that_disagree_on_active_status_stay_ambiguous(registry):
    """If one registration is switched off and the other is live, the answer
    genuinely differs between them, so collapsing would be a decision rather
    than a tidy-up."""
    registry([_company_account(is_active=False)])

    result = engine.classify_receiver(_extraction())

    assert result["receiver_match_ambiguous"] is True
    assert result["receiver_type"] == "unknown"


def test_duplicates_that_disagree_on_verification_stay_ambiguous(registry):
    """Same reasoning for verification status."""
    registry([_company_account(verification_status="UNVERIFIED")])

    result = engine.classify_receiver(_extraction())

    assert result["receiver_match_ambiguous"] is True
    assert result["receiver_type"] == "unknown"


def test_an_unregistered_upi_is_still_not_a_company(registry):
    """The collapse must not turn a stranger into the company."""
    registry([_company_account()])

    result = engine.classify_receiver(_extraction(receiver_upi_id="stranger@okaxis"))

    assert result["receiver_type"] == "unknown"
    assert result["receiver_match_ambiguous"] is False


def test_a_single_registration_is_unaffected(monkeypatch, tmp_path):
    """The ordinary path keeps working and reports no duplicates."""
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", COMPANY_UPI)
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", "J Ravinder")
    monkeypatch.setenv("PAYMENT_REFERRER_RECEIVERS_JSON", "")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", _write_registry(tmp_path, []))

    result = engine.classify_receiver(_extraction())

    assert result["receiver_type"] == "company"
    assert result["receiver_match_ambiguous"] is False
    assert not result.get("receiver_match_duplicates")
