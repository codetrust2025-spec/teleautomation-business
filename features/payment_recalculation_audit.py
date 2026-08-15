"""Append-only record of every change to a proof-derived received total.

Money moving without an explanation is the thing this file exists to prevent.
Entries are only ever appended: a correction is a new entry, never an edit to
an old one, so the history of what a candidate's received total was and why it
changed stays reconstructable.
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


def _audit_file() -> str:
    return os.environ.get(
        "PAYMENT_RECALCULATION_AUDIT_FILE",
        os.path.join(DATA_DIR, "payment_recalculation_audit.json"),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    path = _audit_file()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("entries", [])
    return data


def record_recalculation(
    *,
    candidate_id: str,
    candidate_name: str = "",
    previous_total: int,
    new_total: int,
    trigger: str,
    reason: str,
    reviewer: str = "system",
    proof_id: str = "",
    proof_change: str = "",
    extracted_amount: Any = None,
    verified_amount: Any = None,
    utr: str = "",
    proof_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Append one immutable recalculation record and return it.

    `trigger` says what happened (proof_added, proof_rejected, proof_deleted,
    proof_replaced, manual_correction, backfill). `reason` is free text meant
    to be read by a person auditing the change later.
    """
    entry = {
        "entry_id": f"prc_{uuid.uuid4().hex[:16]}",
        "candidate_id": str(candidate_id or ""),
        "candidate_name": str(candidate_name or ""),
        "previous_received_total": max(0, int(previous_total or 0)),
        "new_received_total": max(0, int(new_total or 0)),
        "delta": int(new_total or 0) - int(previous_total or 0),
        "trigger": str(trigger or ""),
        "proof_change": str(proof_change or ""),
        "proof_id": str(proof_id or ""),
        "proof_ids": [str(value) for value in (proof_ids or [])],
        "extracted_amount": (
            None if extracted_amount is None else int(extracted_amount or 0)
        ),
        "verified_amount": (
            None if verified_amount is None else int(verified_amount or 0)
        ),
        "utr": str(utr or ""),
        "reviewer": str(reviewer or "system"),
        "reason": str(reason or ""),
        "recorded_at": _now(),
    }
    path = _audit_file()
    parent = os.path.dirname(path)
    with _lock:
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = _load()
        data["entries"].append(entry)
        data["updated_at"] = entry["recorded_at"]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return entry


def entries(*, candidate_id: str = "") -> list[dict[str, Any]]:
    rows = list(_load().get("entries") or [])
    if candidate_id:
        rows = [row for row in rows if row.get("candidate_id") == str(candidate_id)]
    return rows
