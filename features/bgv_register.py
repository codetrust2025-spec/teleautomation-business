"""BGV consultancy cases, kept out of the company's own books.

A BGV certificate is performed and billed by a third party. The company collects
the fee and passes it on, so that money is never revenue: it earns nothing for
the company, the referrer or the handler, and it must not reach salary,
recovery, expenses, payout or profit.

Isolation here is structural, not cosmetic. This module is a leaf — no earnings
calculation imports it, and it reaches into the payment ledger only to read the
BGV slice of an existing transaction. Nothing it stores can flow back into
normal accounting, because there is no path for it to travel.

A collection never creates a payment. The candidate made one transfer; the
allocation engine decides how much of it settles BGV, and a collection entry
records that reference. Two credits for one transfer is the failure this
avoids.

Cases key on the canonical profile, so cloned candidate rows cannot disagree
about how much BGV is owed.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.config import DATA_DIR

_lock = RLock()

AWAITING_COLLECTION = "AWAITING_COLLECTION"
PARTIALLY_COLLECTED = "PARTIALLY_COLLECTED"
COLLECTED = "COLLECTED"
PAYMENT_PENDING = "PAYMENT_PENDING"
PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
SETTLED = "SETTLED"
SENT_TO_CONSULTANCY = "SENT_TO_CONSULTANCY"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
CANCELLED = "CANCELLED"
MANUAL_REVIEW = "MANUAL_REVIEW"

STATUSES = {
    AWAITING_COLLECTION, PARTIALLY_COLLECTED, COLLECTED, PAYMENT_PENDING,
    PARTIALLY_SETTLED, SETTLED, SENT_TO_CONSULTANCY, IN_PROGRESS, COMPLETED,
    CANCELLED, MANUAL_REVIEW,
}

# Statuses an operator sets deliberately; the rest are derived from the money.
MANUAL_STATUSES = {SENT_TO_CONSULTANCY, IN_PROGRESS, COMPLETED, CANCELLED,
                   MANUAL_REVIEW}


def _file() -> str:
    return os.environ.get(
        "BGV_REGISTER_FILE", os.path.join(DATA_DIR, "bgv_register.json")
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    try:
        with open(_file(), encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("cases", [])
    return data


def _save(data: dict[str, Any]) -> None:
    path = _file()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    data["updated_at"] = _now()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def profile_key(name: str, phone: str) -> str:
    """Canonical identity, so clone rows resolve to one case."""
    from features import candidate_store

    return "{}|{}".format(
        candidate_store._normalise_candidate_name_key(name or ""),
        candidate_store.candidate_phone_identity(phone),
    )


def _audit(case: dict[str, Any], action: str, actor: str, detail: dict) -> None:
    case.setdefault("audit", []).append({
        "at": _now(), "action": action, "actor": actor, **detail,
    })


def _verified_total(rows: list[dict[str, Any]]) -> int:
    return sum(
        max(0, int(row.get("amount") or 0))
        for row in rows or []
        if row.get("verified")
    )


def derive(case: dict[str, Any]) -> dict[str, Any]:
    """Money, balances and status computed from what the case actually holds."""
    expected = max(0, int(case.get("bgv_expected") or 0))
    collected = _verified_total(case.get("collections"))
    settled = _verified_total(case.get("settlements"))
    outstanding = max(0, expected - collected)
    payable = max(0, collected - settled)

    # A negative balance would mean more went out than came in, which is a
    # refund or an adjustment and needs its own record rather than a minus sign.
    over_settled = max(0, settled - collected)

    manual = str(case.get("status") or "")
    if manual in MANUAL_STATUSES:
        status = manual
    elif collected <= 0:
        status = AWAITING_COLLECTION
    elif outstanding > 0:
        status = PARTIALLY_COLLECTED
    elif settled <= 0:
        status = PAYMENT_PENDING
    elif payable > 0:
        status = PARTIALLY_SETTLED
    else:
        status = SETTLED

    return {
        **case,
        "bgv_expected": expected,
        "bgv_collected": collected,
        "bgv_outstanding": outstanding,
        "paid_to_consultancy": settled,
        "consultancy_payable": payable,
        "over_settled": over_settled,
        "needs_adjustment": over_settled > 0,
        "status": status,
        # Stated rather than computed, so anyone reading a case sees the rule.
        "company_earning": 0,
        "referral_earning": 0,
        "handler_earning": 0,
    }


def upsert_case(
    *,
    candidate_id: str,
    candidate_name: str,
    phone: str = "",
    bgv_expected: int,
    consultancy: str = "",
    service_description: str = "",
    notes: str = "",
    actor: str = "administrator",
) -> dict[str, Any]:
    """Create the profile's case, or update its standing details.

    One case per profile. Calling this again adjusts the case rather than
    opening a second one, so a repeated correction run cannot fork a candidate's
    BGV history in two.
    """
    key = profile_key(candidate_name, phone)
    with _lock:
        data = _load()
        case = next((c for c in data["cases"] if c.get("profile_key") == key), None)
        if case is None:
            case = {
                "case_id": f"bgv_{uuid.uuid4().hex[:12]}",
                "profile_key": key,
                "canonical_candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "phone": phone,
                "consultancy": consultancy,
                "service_description": service_description,
                "bgv_expected": max(0, int(bgv_expected or 0)),
                "collections": [],
                "settlements": [],
                "status": AWAITING_COLLECTION,
                "notes": notes,
                "created_by": actor,
                "reviewed_by": "",
                "created_at": _now(),
                "updated_at": _now(),
                "audit": [],
            }
            _audit(case, "case_created", actor, {
                "bgv_expected": case["bgv_expected"], "consultancy": consultancy,
            })
            data["cases"].append(case)
        else:
            changes = {}
            for field, value in (
                ("bgv_expected", max(0, int(bgv_expected or 0))),
                ("consultancy", consultancy), ("service_description", service_description),
                ("notes", notes),
            ):
                if value not in (None, "") and case.get(field) != value:
                    changes[field] = {"from": case.get(field), "to": value}
                    case[field] = value
            if changes:
                case["updated_at"] = _now()
                _audit(case, "case_updated", actor, {"changes": changes})
        _save(data)
        return derive(case)


def record_collection(
    *,
    case_id: str,
    amount: int,
    payment_id: str = "",
    transaction_reference: str = "",
    transaction_id: str = "",
    occurred_on: str = "",
    proof_id: str = "",
    verified: bool = False,
    actor: str = "administrator",
    note: str = "",
) -> dict[str, Any]:
    """Record the BGV share of a payment the candidate already made.

    This does not create a payment. It points at one that exists and states how
    much of it settles BGV, which is why a transaction can appear here and in
    the candidate's service total without being counted twice — they are two
    slices of one transfer, not two transfers.
    """
    with _lock:
        data = _load()
        case = next((c for c in data["cases"] if c.get("case_id") == case_id), None)
        if case is None:
            raise ValueError(f"No BGV case {case_id}")
        key = transaction_reference or transaction_id or payment_id or proof_id
        existing = next(
            (row for row in case["collections"] if row.get("dedupe_key") == key), None
        ) if key else None
        if existing:
            return derive(case)
        entry = {
            "collection_id": f"col_{uuid.uuid4().hex[:12]}",
            "dedupe_key": key,
            "amount": max(0, int(amount or 0)),
            "payment_id": payment_id,
            "transaction_reference": transaction_reference,
            "transaction_id": transaction_id,
            "occurred_on": occurred_on,
            "proof_id": proof_id,
            "verified": bool(verified),
            "note": note,
            "recorded_by": actor,
            "recorded_at": _now(),
        }
        case["collections"].append(entry)
        case["updated_at"] = _now()
        _audit(case, "collection_recorded", actor, {
            "collection_id": entry["collection_id"], "amount": entry["amount"],
            "transaction_reference": transaction_reference, "verified": entry["verified"],
        })
        _save(data)
        return derive(case)


def record_settlement(
    *,
    case_id: str,
    amount: int,
    transaction_reference: str = "",
    occurred_on: str = "",
    proof_id: str = "",
    verified: bool = False,
    actor: str = "administrator",
    note: str = "",
) -> dict[str, Any]:
    """Record money paid onward to the consultancy.

    A settlement is not a company expense. The money was never the company's;
    it is being handed to whoever performed the check, so it belongs to the
    payable balance and nowhere else.
    """
    with _lock:
        data = _load()
        case = next((c for c in data["cases"] if c.get("case_id") == case_id), None)
        if case is None:
            raise ValueError(f"No BGV case {case_id}")
        key = transaction_reference or proof_id
        existing = next(
            (row for row in case["settlements"] if row.get("dedupe_key") == key), None
        ) if key else None
        if existing:
            return derive(case)
        entry = {
            "settlement_id": f"set_{uuid.uuid4().hex[:12]}",
            "dedupe_key": key,
            "amount": max(0, int(amount or 0)),
            "transaction_reference": transaction_reference,
            "occurred_on": occurred_on,
            "proof_id": proof_id,
            "verified": bool(verified),
            "note": note,
            "recorded_by": actor,
            "recorded_at": _now(),
        }
        case["settlements"].append(entry)
        case["updated_at"] = _now()
        _audit(case, "settlement_recorded", actor, {
            "settlement_id": entry["settlement_id"], "amount": entry["amount"],
            "transaction_reference": transaction_reference, "verified": entry["verified"],
        })
        _save(data)
        return derive(case)


def set_status(*, case_id: str, status: str, actor: str, reason: str = "") -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"Unknown BGV status: {status}")
    with _lock:
        data = _load()
        case = next((c for c in data["cases"] if c.get("case_id") == case_id), None)
        if case is None:
            raise ValueError(f"No BGV case {case_id}")
        previous = case.get("status")
        case["status"] = status
        case["reviewed_by"] = actor
        case["updated_at"] = _now()
        _audit(case, "status_changed", actor,
               {"from": previous, "to": status, "reason": reason})
        _save(data)
        return derive(case)


def get_case(case_id: str) -> dict[str, Any] | None:
    case = next((c for c in _load()["cases"] if c.get("case_id") == case_id), None)
    return derive(case) if case else None


def case_for_profile(name: str, phone: str = "") -> dict[str, Any] | None:
    key = profile_key(name, phone)
    case = next((c for c in _load()["cases"] if c.get("profile_key") == key), None)
    return derive(case) if case else None


def list_cases(*, status: str = "", search: str = "") -> list[dict[str, Any]]:
    cases = [derive(c) for c in _load()["cases"]]
    if status:
        cases = [c for c in cases if c["status"] == status]
    if search:
        needle = search.strip().lower()
        cases = [
            c for c in cases
            if needle in str(c.get("candidate_name") or "").lower()
            or needle in str(c.get("consultancy") or "").lower()
        ]
    cases.sort(key=lambda c: (-c["bgv_outstanding"], c.get("candidate_name") or ""))
    return cases


def dashboard() -> dict[str, Any]:
    cases = list_cases()
    active = [c for c in cases if c["status"] not in {COMPLETED, CANCELLED}]
    return {
        "total_cases": len(cases),
        "active_cases": len(active),
        "completed_cases": sum(1 for c in cases if c["status"] == COMPLETED),
        "cancelled_cases": sum(1 for c in cases if c["status"] == CANCELLED),
        "needs_review": sum(
            1 for c in cases if c["status"] == MANUAL_REVIEW or c["needs_adjustment"]
        ),
        "expected_total": sum(c["bgv_expected"] for c in cases),
        "collected_total": sum(c["bgv_collected"] for c in cases),
        "outstanding_total": sum(c["bgv_outstanding"] for c in cases),
        "paid_to_consultancy_total": sum(c["paid_to_consultancy"] for c in cases),
        "consultancy_payable_total": sum(c["consultancy_payable"] for c in cases),
        # Restated on every payload so no caller has to infer it.
        "company_earning_total": 0,
        "referral_earning_total": 0,
        "cases": cases,
    }


def csv_rows(cases: list[dict[str, Any]]) -> str:
    header = [
        "case_id", "candidate", "consultancy", "service", "bgv_expected",
        "bgv_collected", "bgv_outstanding", "paid_to_consultancy",
        "consultancy_payable", "status", "company_earning", "referral_earning",
        "collection_utrs", "settlement_utrs", "created_at", "updated_at",
    ]
    lines = [",".join(header)]
    for case in cases:
        values = [
            case["case_id"], case.get("candidate_name", ""), case.get("consultancy", ""),
            case.get("service_description", ""), case["bgv_expected"],
            case["bgv_collected"], case["bgv_outstanding"],
            case["paid_to_consultancy"], case["consultancy_payable"], case["status"],
            case["company_earning"], case["referral_earning"],
            " ".join(
                str(c.get("transaction_reference") or "")
                for c in case.get("collections") or []
            ),
            " ".join(
                str(s.get("transaction_reference") or "")
                for s in case.get("settlements") or []
            ),
            case.get("created_at", ""), case.get("updated_at", ""),
        ]
        lines.append(",".join(f'"{str(v).replace(chr(34), chr(34) * 2)}"' for v in values))
    return "\n".join(lines)
