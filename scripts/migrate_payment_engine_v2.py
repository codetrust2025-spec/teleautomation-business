"""Backfill the JSON payment ledger to the V2 allocation schema.

Dry-run is the default. Use ``--apply`` during a maintenance window after
backing up DATA_DIR. Historical screenshots are never reclassified: legacy
company credits remain company credits and legacy referrer recoveries are
split only when the old row contains enough receiver/referrer information.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import payment_verification_engine as engine
from features import referrer_registry


def migrate(data: dict) -> tuple[dict, dict[str, int]]:
    migrated = json.loads(json.dumps(data if isinstance(data, dict) else {}))
    migrated["schema_version"] = 2
    migrated.setdefault("evidence", [])
    migrated.setdefault("payments", [])
    migrated.setdefault("entitlements", [])
    rows = migrated.setdefault("entries", [])
    counts = {"entries": len(rows), "backfilled": 0, "left_pending": 0}
    for row in rows:
        if row.get("transaction_type"):
            continue
        action = str(row.get("action") or "")
        amount_minor = engine._minor_units(row.get("amount"))
        if action == "company_credit":
            row["transaction_type"] = "CANDIDATE_FEE_RECEIVED_BY_COMPANY"
            allocations = engine._allocation_fields(
                amount_minor=amount_minor,
                receiver_type="company",
                purpose="candidate_payment",
                referrer=str(row.get("referrer") or ""),
            )
        elif action == "referrer_recovery" and row.get("referrer"):
            row["transaction_type"] = "CANDIDATE_FEE_RECEIVED_BY_REFERRER"
            allocations = engine._allocation_fields(
                amount_minor=amount_minor,
                receiver_type="referrer",
                purpose="candidate_payment",
                referrer=str(row.get("referrer") or ""),
            )
        elif action == "approved_expense":
            row["transaction_type"] = "COMMISSION_PAYOUT"
            allocations = engine._allocation_fields(
                amount_minor=amount_minor,
                receiver_type="referrer",
                purpose="handler_payout",
                referrer=str(row.get("referrer") or ""),
            )
        else:
            row["transaction_type"] = "PAYOUT_ADJUSTMENT"
            row["settlement_status"] = "DISPUTED"
            row["migration_note"] = "Historical row requires authorized review."
            counts["left_pending"] += 1
            continue
        row.update(allocations)
        row["gross_amount"] = engine._rupees(allocations["gross_amount_minor"])
        row["company_share"] = engine._rupees(allocations["company_share_minor"])
        row["referrer_share"] = engine._rupees(allocations["referrer_share_minor"])
        row["amount_already_received_by_referrer"] = engine._rupees(
            allocations["amount_already_received_by_referrer_minor"]
        )
        row["recoverable_company_share"] = engine._rupees(
            allocations["recoverable_company_share_minor"]
        )
        row["total_payout_adjustment"] = engine._rupees(
            allocations["total_payout_adjustment_minor"]
        )
        row.setdefault("ledger_entry_id", f"legacy_{row.get('id') or ''}")
        row.setdefault("settlement_status", "PENDING")
        row.setdefault("reversal_of_entry_id", "")
        row.setdefault("created_by", "payment_engine_v2_migration")
        counts["backfilled"] += 1
    migrated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return migrated, counts


def registry_backfill() -> tuple[dict, dict[str, int]]:
    """Normalize persisted account rows without materializing name-only matches."""
    records_by_id: dict[str, dict] = {}
    current_rows = referrer_registry._account_rows()
    seed_payload = referrer_registry._read_json(
        referrer_registry._ACCOUNT_SEED_FILE, {"accounts": []}
    )
    seed_rows = (
        seed_payload.get("accounts")
        if isinstance(seed_payload, dict)
        else seed_payload
    ) or []
    # Seed rows are administrator-reviewed receiver identifiers. Merge them
    # by account ID only when their existing canonical referrer resolves.
    # Runtime/admin rows always win and no referrer record is created here.
    rows_by_id = {
        str(row.get("id") or ""): dict(row)
        for row in seed_rows
        if isinstance(row, dict) and str(row.get("id") or "")
    }
    rows_by_id.update(
        {
            str(row.get("id") or ""): dict(row)
            for row in current_rows
            if isinstance(row, dict) and str(row.get("id") or "")
        }
    )
    for row in rows_by_id.values():
        record = referrer_registry._normalized_account(dict(row))
        if record.get("owner_type") == "REFERRER":
            resolved = referrer_registry.resolve_referrer(
                record.get("referrer_id")
                or record.get("account_holder_name")
            )
            if resolved is None:
                record["verification_status"] = "UNVERIFIED"
            else:
                record["referrer_id"] = resolved["id"]
        records_by_id.setdefault(str(record.get("id") or ""), record)
    records = [row for key, row in records_by_id.items() if key]
    conflicts = engine.receiver_registry_conflicts()
    return (
        {
            "schema_version": 1,
            "accounts": records,
            "conflicts": conflicts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "receiver_accounts": len(records),
            "verified_accounts": sum(
                1 for row in records if row.get("verification_status") == "VERIFIED"
            ),
            "unverified_accounts": sum(
                1 for row in records if row.get("verification_status") != "VERIFIED"
            ),
            "registry_conflicts": len(conflicts),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = engine._ledger_file()
    data = engine._load_ledger()
    migrated, counts = migrate(data)
    registry_path = engine._receiver_registry_file()
    registry, registry_counts = registry_backfill()
    referrers = referrer_registry.list_referrers(include_inactive=True)
    pavan = referrer_registry.resolve_referrer("Referrer One")
    print(
        json.dumps(
            {
                "path": path,
                "registry_path": registry_path,
                "referrer_registry_path": referrer_registry._referrers_file(),
                "apply": args.apply,
                "referrer_count": len(referrers),
                "pavan_referrer_id": (pavan or {}).get("id", ""),
                **counts,
                **registry_counts,
            },
            indent=2,
        )
    )
    if not args.apply:
        return 0
    referrer_registry.materialize_current_referrers(
        actor="payment_engine_v2_migration"
    )
    if os.path.exists(path):
        backup = f"{path}.pre-v2"
        shutil.copy2(path, backup)
        print(f"backup={backup}")
    engine._save_ledger(migrated)
    if os.path.exists(registry_path):
        registry_backup = f"{registry_path}.pre-v2"
        shutil.copy2(registry_path, registry_backup)
        print(f"registry_backup={registry_backup}")
    registry_parent = os.path.dirname(registry_path)
    if registry_parent:
        os.makedirs(registry_parent, exist_ok=True)
    registry_tmp = f"{registry_path}.tmp"
    with open(registry_tmp, "w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    os.replace(registry_tmp, registry_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
