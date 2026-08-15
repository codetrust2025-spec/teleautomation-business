"""Persistent idempotency ledger for cross-service requests."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from threading import Lock

from core.config import DATA_DIR

_PATH = os.path.join(DATA_DIR, "service_inbox.json")
_LOCK = Lock()


def claim(key: str, *, keep: int = 10000) -> bool:
    key = str(key or "").strip()
    if not key:
        return False
    with _LOCK:
        try:
            with open(_PATH, encoding="utf-8") as stream:
                rows = json.load(stream)
            if not isinstance(rows, dict): rows = {}
        except (OSError, json.JSONDecodeError):
            rows = {}
        if key in rows: return False
        rows[key] = datetime.now(timezone.utc).isoformat()
        rows = dict(list(rows.items())[-keep:])
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        temporary = _PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream: json.dump(rows, stream, indent=2)
        os.replace(temporary, _PATH)
        return True


def release(key: str) -> None:
    """Remove a reservation when processing failed, allowing a safe retry."""
    with _LOCK:
        try:
            with open(_PATH, encoding="utf-8") as stream:
                rows = json.load(stream)
            if not isinstance(rows, dict):
                return
        except (OSError, json.JSONDecodeError):
            return
        if rows.pop(str(key or "").strip(), None) is None:
            return
        temporary = _PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(rows, stream, indent=2)
        os.replace(temporary, _PATH)
