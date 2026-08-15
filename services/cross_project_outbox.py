"""Small durable outbox for authenticated cross-project HTTP contracts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from core.config import DATA_DIR

_PATH = os.path.join(DATA_DIR, "cross_project_outbox.json")
_LOCK = Lock()
_MAX_ATTEMPTS = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> list[dict[str, Any]]:
    try:
        with open(_PATH, encoding="utf-8") as stream:
            rows = json.load(stream)
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    temporary = _PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(rows, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, _PATH)


def enqueue(*, event_type: str, idempotency_key: str, payload: dict) -> dict:
    """Persist one event before attempting network delivery."""
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")
    with _LOCK:
        rows = _load()
        for row in rows:
            if row.get("idempotency_key") == key:
                return dict(row)
        now = _now().isoformat()
        row = {
            "event_type": event_type,
            "idempotency_key": key,
            "payload": payload,
            "status": "pending",
            "attempts": 0,
            "next_attempt_at": now,
            "created_at": now,
            "delivered_at": None,
            "last_error": "",
        }
        rows.append(row)
        _save(rows[-10000:])
        return dict(row)


def due(*, event_type: str, limit: int = 50) -> list[dict]:
    now = _now()
    with _LOCK:
        result = []
        for row in _load():
            if row.get("event_type") != event_type or row.get("status") != "pending":
                continue
            try:
                when = datetime.fromisoformat(str(row.get("next_attempt_at")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                when = now
            if when <= now:
                result.append(dict(row))
            if len(result) >= limit:
                break
        return result


def mark_delivered(idempotency_key: str) -> None:
    with _LOCK:
        rows = _load()
        for row in rows:
            if row.get("idempotency_key") == idempotency_key:
                row["status"] = "delivered"
                row["delivered_at"] = _now().isoformat()
                row["last_error"] = ""
                break
        _save(rows)


def mark_failed(idempotency_key: str, error: str) -> None:
    with _LOCK:
        rows = _load()
        for row in rows:
            if row.get("idempotency_key") != idempotency_key:
                continue
            attempts = int(row.get("attempts") or 0) + 1
            row["attempts"] = attempts
            row["last_error"] = str(error or "delivery failed")[:500]
            if attempts >= _MAX_ATTEMPTS:
                row["status"] = "dead"
            else:
                delay = min(3600, 2 ** min(attempts, 11))
                row["next_attempt_at"] = (_now() + timedelta(seconds=delay)).isoformat()
            break
        _save(rows)
