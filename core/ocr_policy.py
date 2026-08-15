"""Global policy switch for local OCR/Tesseract execution.

Every feature that may run Tesseract asks this module first, so turning OCR off
turns it off everywhere — invite extraction, screenshot parsing, payment and
proof reading included. There is deliberately no per-feature override: a hidden
fallback would make "OCR off" untrue.

Resolution order, most specific first:

1. the persisted admin setting, when one has been saved
2. the ``OCR_ENABLED`` environment variable
3. enabled

The persisted value wins so an operator can flip the switch without a redeploy,
while the environment variable still lets a host force a value before anything
has been saved.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any

from core.config import DATA_DIR

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}

_LOCK = threading.RLock()
_MAX_AUDIT_ENTRIES = 200


def _state_file() -> str:
    return os.environ.get("OCR_POLICY_FILE", os.path.join(DATA_DIR, "ocr_policy.json"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce(value: Any) -> bool | None:
    """Interpret a stored or supplied value, or None when it says nothing."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def _read_state() -> dict[str, Any]:
    try:
        with open(_state_file(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(state: dict[str, Any]) -> None:
    path = _state_file()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".ocr-policy-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def env_default() -> bool:
    """The value OCR_ENABLED asks for, before any admin override."""
    resolved = _coerce(os.environ.get("OCR_ENABLED"))
    return True if resolved is None else resolved


def ocr_enabled() -> bool:
    """Return whether any project feature may execute local OCR."""
    stored = _coerce(_read_state().get("enabled"))
    return env_default() if stored is None else stored


def processing_mode() -> str:
    """Mode label the UI shows so users know what is reading their file."""
    return "ocr+ai" if ocr_enabled() else "ai"


def set_ocr_enabled(enabled: bool, *, actor: str, source_ip: str = "") -> dict[str, Any]:
    """Persist the switch and record who changed it, from what, to what."""
    desired = bool(enabled)
    with _LOCK:
        state = _read_state()
        entry = {
            "at": _now(),
            "actor": (actor or "unknown").strip()[:120],
            "previous": ocr_enabled(),
            "new": desired,
            "source_ip": (source_ip or "").strip()[:64],
        }
        audit = [e for e in (state.get("audit") or []) if isinstance(e, dict)]
        audit.append(entry)
        state.update({
            "enabled": desired,
            "updated_at": entry["at"],
            "updated_by": entry["actor"],
            # Newest last; trimmed so the file cannot grow without bound.
            "audit": audit[-_MAX_AUDIT_ENTRIES:],
        })
        _write_state(state)
    return status()


def status() -> dict[str, Any]:
    """Current switch state plus the provenance an admin screen needs."""
    state = _read_state()
    stored = _coerce(state.get("enabled"))
    return {
        "enabled": ocr_enabled(),
        "mode": processing_mode(),
        "source": "admin" if stored is not None else "environment",
        "env_default": env_default(),
        "updated_at": state.get("updated_at") or "",
        "updated_by": state.get("updated_by") or "",
    }


def audit_log(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent changes first."""
    entries = [e for e in (_read_state().get("audit") or []) if isinstance(e, dict)]
    return list(reversed(entries))[: max(0, int(limit or 0))]
