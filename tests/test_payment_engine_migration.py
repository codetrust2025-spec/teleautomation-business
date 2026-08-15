from scripts.migrate_payment_engine_v2 import migrate, registry_backfill


def test_legacy_referrer_recovery_is_split_into_explicit_allocations():
    migrated, counts = migrate(
        {
            "entries": [
                {
                    "id": "legacy-1",
                    "action": "referrer_recovery",
                    "amount": 5000,
                    "referrer": "Pawan",
                }
            ]
        }
    )

    row = migrated["entries"][0]
    assert counts["backfilled"] == 1
    assert row["transaction_type"] == "CANDIDATE_FEE_RECEIVED_BY_REFERRER"
    assert row["amount_already_received_by_referrer"] == 2500
    assert row["recoverable_company_share"] == 2500
    assert row["total_payout_adjustment"] == 5000


def test_unknown_legacy_adjustment_is_not_guessed():
    migrated, counts = migrate(
        {"entries": [{"id": "legacy-2", "action": "unknown_pending", "amount": 5000}]}
    )

    row = migrated["entries"][0]
    assert counts["left_pending"] == 1
    assert row["transaction_type"] == "PAYOUT_ADJUSTMENT"
    assert row["settlement_status"] == "DISPUTED"


def test_registry_backfill_does_not_create_payment_accounts_from_names(
    monkeypatch, tmp_path
):
    empty_seed = tmp_path / "empty-payment-accounts.json"
    empty_seed.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setattr(
        "features.referrer_registry._account_rows",
        lambda: [],
    )
    monkeypatch.setattr(
        "features.referrer_registry._ACCOUNT_SEED_FILE",
        str(empty_seed),
    )
    registry, counts = registry_backfill()

    assert registry["accounts"] == []
    assert counts["receiver_accounts"] == 0
    assert counts["verified_accounts"] == 0
