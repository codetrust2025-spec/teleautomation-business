"""Global switch for Pure Ollama AI Mail Detection.

ON, which is the default, every inbound mail goes to Ollama once the basic
duplicate and direction checks have run. The keyword and routing rules that
decide relevance deterministically are skipped, so nothing is filtered out
before the model has read it. That is the point: those rules are what dropped a
real interview reminder as NO_RECRUITMENT_ROUTING_SIGNAL.

OFF, the existing flow runs exactly as before -- prefilter, routing gate,
keyword cues and all. Nothing about the current system is removed, and the
switch is reversible at any time.

ON also leaves post-classification intent with Ollama: legacy JD/digest,
sender-domain and semantic keyword vetoes cannot replace its verdict. OFF
retains those legacy decisions. In both modes the hard safety checks remain:
Candidate match, date/time and timezone validation, payment, duplicate,
conflict and the persistence re-read all still have to pass, and nothing is
recorded as Auto Booked until a slot is actually stored.

Resolution order, most specific first:

1. the persisted admin setting, when one has been saved
2. the ``PURE_OLLAMA_MAIL_DETECTION`` environment variable
3. enabled

The persisted value wins so an operator can flip the switch without a redeploy,
while the environment variable still lets a host force a value before anything
has been saved. A fresh install has neither, and so starts ON.
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
    return os.environ.get(
        "PURE_OLLAMA_POLICY_FILE", os.path.join(DATA_DIR, "pure_ollama_policy.json"),
    )


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
    handle, tmp = tempfile.mkstemp(prefix=".pure-ollama-", suffix=".tmp", dir=directory)
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
    """What PURE_OLLAMA_MAIL_DETECTION asks for, before any admin override.

    Defaults to True: a fresh install with no saved setting and no environment
    variable starts with Pure Ollama detection ON.
    """
    resolved = _coerce(os.environ.get("PURE_OLLAMA_MAIL_DETECTION"))
    return True if resolved is None else resolved


def pure_ollama_enabled() -> bool:
    """Whether mail goes straight to Ollama instead of through the keyword gate."""
    stored = _coerce(_read_state().get("enabled"))
    return env_default() if stored is None else stored


def detection_mode() -> str:
    """Mode label the UI shows so an operator knows which flow is running."""
    return "pure-ollama" if pure_ollama_enabled() else "rules-first"


def set_pure_ollama_enabled(enabled: bool, *, actor: str, source_ip: str = "") -> dict[str, Any]:
    """Persist the switch and record who changed it, from what, to what."""
    desired = bool(enabled)
    with _LOCK:
        state = _read_state()
        entry = {
            "at": _now(),
            "actor": (actor or "unknown").strip()[:120],
            "previous": pure_ollama_enabled(),
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
        "enabled": pure_ollama_enabled(),
        "mode": detection_mode(),
        "source": "admin" if stored is not None else "environment",
        "env_default": env_default(),
        "updated_at": state.get("updated_at") or "",
        "updated_by": state.get("updated_by") or "",
    }


def audit_log(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent changes first."""
    entries = [e for e in (_read_state().get("audit") or []) if isinstance(e, dict)]
    return list(reversed(entries))[: max(0, int(limit or 0))]
