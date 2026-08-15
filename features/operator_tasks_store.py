"""Operator task checklist — JSON on disk (optional Postgres later)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

from core.config import DATA_DIR

_FILE = os.path.join(DATA_DIR, "operator_tasks.json")
_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    with _lock:
        if not os.path.isfile(_FILE):
            return []
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            if isinstance(data, dict):
                tasks = data.get("tasks")
                if isinstance(tasks, list):
                    return [row for row in tasks if isinstance(row, dict)]
        except (OSError, json.JSONDecodeError):
            pass
    return []


def _save(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    payload = {"tasks": rows, "updated_at": _now_iso()}
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


def list_tasks(*, include_done: bool = False, reference: str | None = None) -> list[dict]:
    ref = (reference or "").strip().lower()
    out: list[dict] = []
    for task in _load():
        if not include_done and task.get("done"):
            continue
        if ref:
            task_ref = (task.get("reference") or "").strip().lower()
            if task_ref != ref:
                continue
        out.append(dict(task))
    return out


def add_task(*, reference: str, label: str, date: str | None = None) -> dict:
    row = {
        "id": uuid.uuid4().hex[:10],
        "reference": (reference or "").strip(),
        "label": (label or "").strip(),
        "date": (date or "")[:10],
        "done": False,
        "created_at": _now_iso(),
    }
    rows = _load()
    rows.append(row)
    _save(rows)
    return row
