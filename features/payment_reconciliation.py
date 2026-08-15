"""System-wide reconciliation of recorded money against verified evidence.

Read-only. It classifies and recommends; it never writes. Applying a correction
is a separate, deliberate act, because most disagreements here are not errors in
the money — they are gaps in the evidence, and those two need opposite handling.

The unit is the canonical profile, not the row. Interview slots are stored as
cloned candidate rows carrying copies of the same proof, so reconciling per row
would count one payment many times.
"""
from __future__ import annotations

import collections
from typing import Any

from features import candidate_store, payment_allocation, payment_receipts
from features.candidate_attachments import partition_candidate_attachments

EXACT_MATCH = "EXACT_MATCH"
EXTRACTOR_DEFECT_CORRECTED = "EXTRACTOR_DEFECT_CORRECTED"
GENUINE_MISMATCH = "GENUINE_MISMATCH"
DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
MISSING_EVIDENCE = "MISSING_EVIDENCE"
ADMIN_CONFIRMED_NOT_PAID = "ADMIN_CONFIRMED_NOT_PAID"
LEGACY_INCOMPLETE_COVERAGE = "LEGACY_INCOMPLETE_COVERAGE"
BGV_ALLOCATION_ISSUE = "BGV_ALLOCATION_ISSUE"
UNALLOCATED_EXCESS = "UNALLOCATED_EXCESS"
SAFE_AUTOMATIC_CORRECTION = "SAFE_AUTOMATIC_CORRECTION"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

# Classifications an unattended job may act on. Everything else is a judgement
# about money that only a person should make.
AUTO_CORRECTABLE = frozenset({SAFE_AUTOMATIC_CORRECTION})


def _profile_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        candidate_store._normalise_candidate_name_key(row.get("name") or ""),
        candidate_store.candidate_phone_identity(row.get("phone")),
    )


def _classify(*, recorded, proof_total, adjudicated, unique, allocation,
              statuses, admin_voided, duplicates) -> tuple[str, str]:
    """One label per profile, describing where its money stands today.

    A historical void is deliberately not considered here. Someone confirming
    months ago that a payment never happened says nothing about whether the
    profile balances now, and letting that fact win would hide a live mismatch
    behind a settled one. It travels as a note instead — see `_notes`.
    """
    if duplicates:
        return (DUPLICATE_TRANSACTION,
                f"{duplicates} duplicate transaction reference(s) present; only "
                "one credit is counted.")
    if allocation.get("needs_excess_review"):
        return (UNALLOCATED_EXCESS,
                f"₹{allocation['unallocated_excess']:,} received beyond service "
                "and BGV expectations needs an administrator to classify it.")
    if not adjudicated:
        if recorded > 0:
            return (LEGACY_INCOMPLETE_COVERAGE,
                    "Money recorded before proof capture existed. Leave as is and "
                    "upload the original receipt to bring it under proof control.")
        return (EXACT_MATCH, "No money recorded and none claimed.")
    if statuses.get(payment_receipts.PROOF_STATUS_NEEDS_REVIEW):
        return (MISSING_EVIDENCE,
                "Evidence exists but cannot be relied on — a file is unreadable "
                "or an amount is awaiting review.")
    if proof_total == recorded:
        return (EXACT_MATCH, "Recorded amount matches verified evidence exactly.")
    if proof_total > recorded:
        return (SAFE_AUTOMATIC_CORRECTION,
                f"Verified evidence supports ₹{proof_total:,}, more than the "
                f"₹{recorded:,} recorded. Raising it removes no money.")
    return (GENUINE_MISMATCH,
            f"Verified evidence accounts for only ₹{proof_total:,} of the "
            f"₹{recorded:,} recorded. Do not reduce without confirming the "
            "shortfall is real rather than uncaptured evidence.")


def _notes(*, admin_voided: int, duplicates: int, statuses: dict) -> list[str]:
    """Facts worth surfacing that must not displace the primary classification."""
    notes: list[str] = []
    if admin_voided:
        notes.append(
            f"Contains {admin_voided} historical ADMIN_CONFIRMED_NOT_PAID "
            "transaction(s), preserved and excluded from totals."
        )
    if duplicates:
        notes.append(
            f"{duplicates} transaction reference(s) appear more than once; "
            "counted as one credit."
        )
    review = statuses.get(payment_receipts.PROOF_STATUS_NEEDS_REVIEW) or 0
    if review:
        notes.append(f"{review} proof(s) awaiting review.")
    return notes


def profile_rows() -> list[dict[str, Any]]:
    """One reconciliation record per canonical profile."""
    from features import payment_verification_engine as pve

    rows = candidate_store._load().get("candidates") or []
    ledger = pve._load_ledger()
    voided_by_candidate: dict[str, int] = collections.Counter()
    for payment in ledger.get("payments") or []:
        if payment.get("admin_disposition") == "ADMIN_CONFIRMED_NOT_PAID":
            key = str(payment.get("candidate_id") or payment.get("source_entity_id") or "")
            if key:
                voided_by_candidate[key] += 1

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_profile_key(row), []).append(row)

    records: list[dict[str, Any]] = []
    for members in groups.values():
        representative = max(members, key=lambda m: int(m.get("payment") or 0))
        proofs: list[dict[str, Any]] = []
        for member in members:
            proofs.extend(partition_candidate_attachments(member)["payment_proofs"])

        recorded = max(int(m.get("payment") or 0) for m in members)
        unique = payment_receipts.unique_verified_proofs(proofs)
        proof_total = sum(payment_receipts.proof_amount(p) for p in unique)
        adjudicated = payment_receipts.has_proof_evidence(proofs)
        statuses = payment_receipts.status_counts(proofs)

        # Same reference seen more than once is one transaction, not two.
        seen: collections.Counter = collections.Counter()
        for proof in proofs:
            kind, value = payment_receipts.proof_identity(proof)
            if kind != "attachment_id" and value:
                seen[value] += 1
        duplicates = sum(1 for count in seen.values() if count > 1)

        allocation = candidate_store.payment_allocation_for(representative)
        admin_voided = sum(
            voided_by_candidate.get(str(m.get("id")), 0) for m in members
        )
        classification, recommendation = _classify(
            recorded=recorded, proof_total=proof_total, adjudicated=adjudicated,
            unique=unique, allocation=allocation, statuses=statuses,
            admin_voided=admin_voided, duplicates=duplicates,
        )
        records.append({
            "candidate_id": str(representative.get("id") or ""),
            "candidate_name": str(representative.get("name") or ""),
            "reference": str(representative.get("reference") or ""),
            "row_ids": [str(m.get("id") or "") for m in members],
            "service_expected": allocation["service_expected"],
            "bgv_expected": allocation["bgv_expected"],
            "recorded_received": recorded,
            "verified_transaction_total": proof_total,
            "service_allocation": allocation["service_received"],
            "bgv_allocation": allocation["bgv_received"],
            "bgv_outstanding": allocation["bgv_outstanding"],
            "unallocated_excess": allocation["unallocated_excess"],
            "outstanding": allocation["service_outstanding"] + allocation["bgv_outstanding"],
            "difference": proof_total - recorded,
            "referral": candidate_store.referrer_commission_amount(representative),
            "company_share": max(
                0,
                allocation["service_received"]
                - candidate_store.referrer_commission_amount(representative),
            ),
            "proof_ids": [str(p.get("id") or "") for p in unique],
            "utrs": [
                str(p.get("utr_number") or p.get("transaction_id") or "")
                for p in unique
            ],
            "verification_states": sorted({
                str(p.get("verification_state") or "") for p in proofs
                if p.get("verification_state")
            }),
            "file_states": sorted({
                payment_receipts.file_availability(p) for p in proofs
            }),
            "proof_status_counts": statuses,
            "duplicate_references": duplicates,
            "admin_voided_payments": admin_voided,
            "proof_controlled": bool(representative.get("payment_proof_controlled")),
            "classification": classification,
            "notes": _notes(admin_voided=admin_voided, duplicates=duplicates,
                            statuses=statuses),
            "has_historical_void": bool(admin_voided),
            "recommended_action": recommendation,
            # A profile carrying an unresolved historical void still needs a
            # person to look at it, even when today's totals balance.
            "auto_correctable": (
                classification in AUTO_CORRECTABLE and not admin_voided
            ),
        })
    records.sort(key=lambda r: (r["classification"], -abs(r["difference"])))
    return records


def preview() -> dict[str, Any]:
    """Read-only reconciliation across every profile."""
    records = profile_rows()
    counts: collections.Counter = collections.Counter(
        r["classification"] for r in records
    )
    return {
        "profiles_checked": len(records),
        "counts": dict(counts),
        "auto_correctable": [r for r in records if r["auto_correctable"]],
        "auto_correctable_count": sum(1 for r in records if r["auto_correctable"]),
        "net_auto_correction": sum(
            r["difference"] for r in records if r["auto_correctable"]
        ),
        "duplicate_transactions": sum(r["duplicate_references"] for r in records),
        "recorded_total": sum(r["recorded_received"] for r in records),
        "verified_total": sum(r["verified_transaction_total"] for r in records),
        "records": records,
    }


def csv_rows(records: list[dict[str, Any]]) -> str:
    header = [
        "candidate", "candidate_id", "classification", "service_expected",
        "bgv_expected", "recorded_received", "verified_transaction_total",
        "service_allocation", "bgv_allocation", "outstanding", "difference",
        "referral", "company_share", "utrs", "proof_ids", "verification_states",
        "file_states", "recommended_action",
    ]
    lines = [",".join(header)]
    for record in records:
        values = [
            record["candidate_name"], record["candidate_id"], record["classification"],
            record["service_expected"], record["bgv_expected"],
            record["recorded_received"], record["verified_transaction_total"],
            record["service_allocation"], record["bgv_allocation"],
            record["outstanding"], record["difference"], record["referral"],
            record["company_share"], " ".join(record["utrs"]),
            " ".join(record["proof_ids"]), " ".join(record["verification_states"]),
            " ".join(record["file_states"]), record["recommended_action"],
        ]
        lines.append(",".join(f'"{str(v).replace(chr(34), chr(34) * 2)}"' for v in values))
    return "\n".join(lines)
