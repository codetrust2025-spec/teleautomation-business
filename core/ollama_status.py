"""Thread-safe, non-sensitive runtime status for the local Ollama gateway."""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()
_last_request_succeeded: bool | None = None
_state: dict[str, Any] = {
    "provider": "ollama",
    "status": "unavailable",
    "diagnostic_status": "NOT_CHECKED",
    "endpoint_reachable": False,
    "service_reachable": False,
    "serviceReachable": False,
    "configured_model": None,
    "primary_model": None,
    "primaryModel": None,
    "model_available": False,
    "primary_model_available": False,
    "primaryModelAvailable": False,
    "required_models": {},
    "fallback_models": {},
    "fallbackModels": {},
    "installed_models": [],
    "missing_models": [],
    "response_time_ms": None,
    "last_checked_at": None,
    "checkedAt": None,
    "last_successful_request_at": None,
    "last_failed_request_at": None,
    "average_response_time_ms": None,
    "successful_requests": 0,
    "failed_requests": 0,
    "error_code": "OLLAMA_NOT_CHECKED",
    "error_message": "Ollama has not been checked yet.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_health(**values: Any) -> dict[str, Any]:
    with _lock:
        # A model-manifest health response proves that the service is reachable;
        # it does not prove that the runner can load or generate.  Do not let a
        # tag refresh erase a newer failed inference and show a false Healthy.
        if values.get("status") == "healthy" and _last_request_succeeded is False:
            values["status"] = "degraded"
            values["error_code"] = _state.get("error_code")
            values["error_message"] = _state.get("error_message")
        _state.update(values)
        checked_at = _now()
        _state["last_checked_at"] = checked_at
        _state["checkedAt"] = checked_at
        return deepcopy(_state)


def record_request_success(duration_ms: int) -> None:
    global _last_request_succeeded
    with _lock:
        completed = int(_state.get("successful_requests") or 0) + 1
        previous = _state.get("average_response_time_ms")
        _state["successful_requests"] = completed
        _state["average_response_time_ms"] = (
            duration_ms if previous is None else round(((float(previous) * (completed - 1)) + duration_ms) / completed)
        )
        _state["last_successful_request_at"] = _now()
        _state["status"] = "healthy"
        _state["endpoint_reachable"] = True
        _state["model_available"] = True
        _state["error_code"] = None
        _state["error_message"] = None
        _last_request_succeeded = True


def record_request_failure(code: str, message: str) -> None:
    global _last_request_succeeded
    with _lock:
        _state["failed_requests"] = int(_state.get("failed_requests") or 0) + 1
        _state["last_failed_request_at"] = _now()
        _state["status"] = "degraded" if _state.get("endpoint_reachable") else "unavailable"
        _state["error_code"] = code
        _state["error_message"] = message
        _last_request_succeeded = False


def snapshot() -> dict[str, Any]:
    with _lock:
        return deepcopy(_state)
