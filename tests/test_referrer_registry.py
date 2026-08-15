import json

import pytest

from features import referrer_registry


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    referrers_path = tmp_path / "referrers.json"
    accounts_path = tmp_path / "accounts.json"
    referrers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "referrers": [
                    {
                        "id": "referrer-pavan-kalyan",
                        "name": "Sample Referrer",
                        "aliases": ["Sample Referrer", "SAMPLE REFERRER TWO"],
                        "is_active": True,
                    },
                    {
                        "id": "referrer-thrilok",
                        "name": "Thrilok",
                        "aliases": [],
                        "is_active": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    accounts_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers_path))
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts_path))
    monkeypatch.setenv(
        "PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json")
    )
    monkeypatch.setattr(
        referrer_registry,
        "_dynamic_reference_names",
        lambda: ["SAMPLE REFERRER", "Thrilok"],
    )
    return referrers_path, accounts_path


def test_existing_pavan_aliases_resolve_to_one_stable_referrer(isolated_registry):
    rows = referrer_registry.list_referrers()

    assert len(rows) == 2
    assert referrer_registry.resolve_referrer("Sample Referrer")["id"] == (
        "referrer-pavan-kalyan"
    )
    assert referrer_registry.resolve_referrer("SAMPLE REFERRER TWO")["id"] == (
        "referrer-pavan-kalyan"
    )


def test_admin_account_lifecycle_masks_identifier_and_preserves_history(
    isolated_registry,
):
    created = referrer_registry.add_payment_account(
        "referrer-pavan-kalyan",
        {
            "account_holder_name": "SAMPLE REFERRER TWO",
            "upi_id": "SampleRef42761 @ okaxis",
            "provider_name": "UPI",
        },
        actor="admin@example.test",
    )

    assert created["masked_upi_id"].endswith("@okaxis")
    assert "upi_id" not in created
    verified = referrer_registry.update_payment_account(
        created["id"],
        {"verification_status": "VERIFIED"},
        actor="admin@example.test",
    )
    assert verified["verification_status"] == "VERIFIED"
    assert verified["verified_by"] == "admin@example.test"
    assert [entry["action"] for entry in verified["history"]] == [
        "CREATED",
        "UPDATED",
    ]
    with pytest.raises(ValueError, match="must be deactivated"):
        referrer_registry.remove_unverified_payment_account(
            created["id"], actor="admin@example.test"
        )


def test_active_identifier_cannot_belong_to_two_referrers(isolated_registry):
    referrer_registry.add_payment_account(
        "referrer-pavan-kalyan",
        {"upi_id": "owner@okaxis"},
        actor="admin",
    )

    with pytest.raises(ValueError, match="already belongs"):
        referrer_registry.add_payment_account(
            "referrer-thrilok",
            {"upi_id": "OWNER @ OKAXIS"},
            actor="admin",
        )
