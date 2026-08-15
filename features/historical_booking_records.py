"""Append-only notes about bookings that went wrong and were never restored.

Some booking incidents cannot be repaired by changing a booking: the interview
time has passed, so there is nothing left to schedule. What remains is the
record. Without one, the roster shows a candidate who simply had no interview
that day, and the reason — a defect, a clash, a decision not to restore — is
lost.

Nothing here touches a live booking. It writes only to its own file.
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

HISTORICAL_CONFLICT_NOT_RESTORED = "HISTORICAL_CONFLICT_NOT_RESTORED"
HISTORICAL_CANCELLED_BY_ORGANISER = "HISTORICAL_CANCELLED_BY_ORGANISER"
HISTORICAL_RESTORED = "HISTORICAL_RESTORED"

DISPOSITIONS = {
    HISTORICAL_CONFLICT_NOT_RESTORED,
    HISTORICAL_CANCELLED_BY_ORGANISER,
    HISTORICAL_RESTORED,
}


def _file() -> str:
    return os.environ.get(
        "HISTORICAL_BOOKING_RECORDS_FILE",
        os.path.join(DATA_DIR, "historical_booking_records.json"),
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
    data.setdefault("records", [])
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


def record(
    *,
    candidate_id: str,
    candidate_name: str,
    disposition: str,
    occurred_on: str,
    scheduled_time: str,
    company: str,
    summary: str,
    reason_not_restored: str,
    reviewer: str,
    calendar_uids: dict[str, str] | None = None,
    booking_ids: dict[str, str] | None = None,
    gmail_message_ids: dict[str, str] | None = None,
    conflicts_with: dict[str, str] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Record one historical booking outcome.

    `idempotency_key` makes re-recording the same incident a no-op, so a repeated
    correction run cannot fill the file with duplicates of one event.
    """
    if disposition not in DISPOSITIONS:
        raise ValueError(f"Unknown disposition: {disposition}")
    key = idempotency_key or f"{candidate_id}:{occurred_on}:{scheduled_time}"

    with _lock:
        data = _load()
        existing = next(
            (r for r in data["records"] if r.get("idempotency_key") == key), None
        )
        if existing:
            return {"record": dict(existing), "created": False}
        entry = {
            "record_id": f"hbr_{uuid.uuid4().hex[:16]}",
            "idempotency_key": key,
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "disposition": disposition,
            "occurred_on": occurred_on,
            "scheduled_time": scheduled_time,
            "company": company,
            "summary": summary,
            "reason_not_restored": reason_not_restored,
            "calendar_uids": calendar_uids or {},
            "booking_ids": booking_ids or {},
            "gmail_message_ids": gmail_message_ids or {},
            "conflicts_with": conflicts_with or {},
            "reviewer": reviewer,
            "recorded_at": _now(),
        }
        data["records"].append(entry)
        _save(data)
        return {"record": dict(entry), "created": True}


def records(*, candidate_id: str = "") -> list[dict[str, Any]]:
    rows = list(_load().get("records") or [])
    if candidate_id:
        rows = [r for r in rows if r.get("candidate_id") == candidate_id]
    return rows
