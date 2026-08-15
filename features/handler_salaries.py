"""Read fixed monthly handler salaries from protected runtime configuration.

The salary store lives under ``DATA_DIR`` and is operational data, not source.
This module deliberately exposes read-only calculations; salary configuration
continues to be managed outside an application release.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from core.config import DATA_DIR


_FILE = os.path.join(DATA_DIR, "handler_salaries.json")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _month_index(value: object) -> int | None:
    """Return a sortable month index for ``YYYY-MM``, or ``None``."""
    match = _MONTH_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    year, month = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        return None
    return year * 12 + month - 1


def _coerce_salary(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _load_records() -> list[tuple[str, dict]]:
    """Load supported salary-store shapes and fail closed on invalid data."""
    try:
        with open(_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    salaries = payload.get("salaries", {}) if isinstance(payload, dict) else {}
    if isinstance(salaries, dict):
        return [
            (str(key or "").strip(), record)
            for key, record in salaries.items()
            if isinstance(record, dict)
        ]
    if isinstance(salaries, list):
        return [
            (str(record.get("reference") or "").strip(), record)
            for record in salaries
            if isinstance(record, dict)
        ]
    return []


def salary_owed_by_handler(month: str | None = None) -> dict[str, dict]:
    """Return fixed salary owed per handler for one month or all active months.

    ``month`` accepts ``YYYY-MM``. When omitted, the result covers every active
    month through the current UTC month (or the configured end month). Invalid
    records are ignored so malformed runtime configuration cannot create a
    financial obligation.
    """
    requested_index: int | None = None
    if month and month != "all":
        requested_index = _month_index(month)
        if requested_index is None:
            return {}

    current_index = _month_index(datetime.now(timezone.utc).strftime("%Y-%m"))
    if current_index is None:  # pragma: no cover - defensive only
        return {}

    result: dict[str, dict] = {}
    for store_key, record in _load_records():
        reference = str(record.get("reference") or store_key).strip()
        key = reference.lower()
        monthly_salary = _coerce_salary(record.get("monthly_salary"))
        active_from = str(record.get("active_from") or "").strip()
        active_until = str(record.get("active_until") or "").strip()
        start_index = _month_index(active_from)
        end_index = _month_index(active_until) if active_until else None

        if not key or not monthly_salary or start_index is None:
            continue
        if end_index is not None and end_index < start_index:
            continue

        if requested_index is not None:
            months_owed = int(
                requested_index >= start_index
                and (end_index is None or requested_index <= end_index)
            )
        else:
            effective_end = min(end_index, current_index) if end_index is not None else current_index
            months_owed = max(0, effective_end - start_index + 1)

        if not months_owed:
            continue
        result[key] = {
            "name": reference,
            "reference": reference,
            "monthly_salary": monthly_salary,
            "months_owed": months_owed,
            "owed": monthly_salary * months_owed,
            "active_from": active_from,
            "active_until": active_until or None,
        }
    return result


def list_salaries() -> list[dict]:
    return [dict(record, reference=record.get("reference") or key) for key, record in _load_records()]


def total_salary_owed(month: str | None = None) -> int:
    return sum(int(row.get("owed") or 0) for row in salary_owed_by_handler(month).values())


def _save_records(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    payload = {
        "salaries": {
            str(row.get("reference") or "").strip().lower(): row
            for row in rows
            if str(row.get("reference") or "").strip()
        }
    }
    temporary = _FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, _FILE)


def set_salary(reference: str, monthly_salary: int, active_from: str, active_until: str = "") -> dict:
    name = str(reference or "").strip()
    salary = _coerce_salary(monthly_salary)
    if not name or not salary or _month_index(active_from) is None:
        raise ValueError("Reference, positive monthly salary, and valid active_from month are required")
    if active_until and _month_index(active_until) is None:
        raise ValueError("active_until must be YYYY-MM")
    rows = [row for row in list_salaries() if str(row.get("reference") or "").casefold() != name.casefold()]
    record = {
        "reference": name,
        "monthly_salary": salary,
        "active_from": active_from,
        "active_until": active_until or "",
    }
    rows.append(record)
    _save_records(rows)
    return record


def delete_salary(reference: str) -> bool:
    name = str(reference or "").strip().casefold()
    rows = list_salaries()
    filtered = [row for row in rows if str(row.get("reference") or "").casefold() != name]
    if len(filtered) == len(rows):
        return False
    _save_records(filtered)
    return True
