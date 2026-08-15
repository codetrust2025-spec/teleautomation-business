"""Structured technical logging — machine-parseable, consistent schema."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

_logger = logging.getLogger("telegram_forward.structured")


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class LogEvent(str, Enum):
    CYCLE_START = "CYCLE_START"
    CYCLE_END = "CYCLE_END"
    CYCLE_RESUME = "CYCLE_RESUME"
    CYCLE_ERROR = "CYCLE_ERROR"
    MSG_VARIANT_READY = "MSG_VARIANT_READY"
    GROUP_SOURCE_EMPTY = "GROUP_SOURCE_EMPTY"
    TELEGRAM_CONNECTED = "TELEGRAM_CONNECTED"
    JOIN_ATTEMPT = "JOIN_ATTEMPT"
    JOIN_SUCCESS = "JOIN_SUCCESS"
    JOIN_FAIL = "JOIN_FAIL"
    JOIN_SKIP = "JOIN_SKIP"
    SEND_SUCCESS = "SEND_SUCCESS"
    SEND_FAIL = "SEND_FAIL"
    SKIP = "SKIP"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FLOOD_WAIT = "FLOOD_WAIT"
    ACCOUNT_SLEEP = "ACCOUNT_SLEEP"
    WORKER_STOP = "WORKER_STOP"
    SESSION_RECONNECT = "SESSION_RECONNECT"
    WAIT = "WAIT"
    GENERIC = "GENERIC"


# Legacy action= strings from account_worker → standard events
ACTION_EVENT_MAP: dict[str, LogEvent] = {
    "cycle_start": LogEvent.CYCLE_START,
    "cycle_resume": LogEvent.CYCLE_RESUME,
    "cycle_end": LogEvent.CYCLE_END,
    "cycle_error": LogEvent.CYCLE_ERROR,
    "message_variant": LogEvent.MSG_VARIANT_READY,
    "connect": LogEvent.TELEGRAM_CONNECTED,
    "wait": LogEvent.RETRY_SCHEDULED,
    "skipped": LogEvent.SKIP,
    "reconnect": LogEvent.SESSION_RECONNECT,
    "account_sleep": LogEvent.ACCOUNT_SLEEP,
    "worker_stop": LogEvent.WORKER_STOP,
    "join": LogEvent.JOIN_ATTEMPT,
    "join_ok": LogEvent.JOIN_SUCCESS,
    "join_fail": LogEvent.JOIN_FAIL,
}


UI_LEVEL_FROM_STD: dict[LogLevel, str] = {
    LogLevel.DEBUG: "info",
    LogLevel.INFO: "info",
    LogLevel.WARN: "warning",
    LogLevel.ERROR: "error",
}

STD_LEVEL_FROM_UI: dict[str, LogLevel] = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "success": LogLevel.INFO,
    "warning": LogLevel.WARN,
    "warn": LogLevel.WARN,
    "error": LogLevel.ERROR,
}


def normalize_level(level: LogLevel | str) -> LogLevel:
    if isinstance(level, LogLevel):
        return level
    return STD_LEVEL_FROM_UI.get(str(level).lower(), LogLevel.INFO)


def ui_level(level: LogLevel | str) -> str:
    std = normalize_level(level)
    return UI_LEVEL_FROM_STD.get(std, "info")


def format_kv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).strip()
    if not s:
        return '""'
    if re.search(r"[\s=|\"']", s):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def format_fields(fields: dict[str, Any] | None) -> str:
    if not fields:
        return ""
    parts: list[str] = []
    for key in sorted(fields.keys()):
        val = fields[key]
        if val is None:
            continue
        parts.append(f"{key}={format_kv_value(val)}")
    return " ".join(parts)


def format_log_line(
    *,
    timestamp: str,
    level: LogLevel,
    account_id: str,
    event: LogEvent,
    cycle: int | None = None,
    fields: dict[str, Any] | None = None,
) -> str:
    """Human-readable + machine-parseable line (no bracket prefixes)."""
    head = [timestamp, level.value, account_id]
    if cycle is not None:
        head.append(f"cycle={cycle}")
    head.append(event.value)
    tail = format_fields(fields)
    return " ".join(head + ([tail] if tail else []))


def infer_event_from_legacy(action: str, reason: str, message: str) -> LogEvent:
    action_l = (action or "").lower()
    if action_l in ACTION_EVENT_MAP:
        return ACTION_EVENT_MAP[action_l]
    msg_l = (message or "").lower()
    if "message variant ready" in msg_l:
        return LogEvent.MSG_VARIANT_READY
    if "telethon connected" in msg_l:
        return LogEvent.TELEGRAM_CONNECTED
    if "no groups loaded" in msg_l:
        return LogEvent.GROUP_SOURCE_EMPTY
    if "starting next cycle" in msg_l:
        return LogEvent.CYCLE_RESUME
    if message.strip().startswith("--- Cycle"):
        return LogEvent.CYCLE_START
    if "join" in msg_l and "fail" in msg_l:
        return LogEvent.JOIN_FAIL
    if "join" in msg_l and ("joined" in msg_l or "success" in msg_l):
        return LogEvent.JOIN_SUCCESS
    if "skipped" in msg_l or "↷" in message:
        return LogEvent.SKIP
    if "flood" in msg_l or reason == "heavy_flood":
        return LogEvent.FLOOD_WAIT
    return LogEvent.GENERIC


def build_log_entry(
    *,
    account_id: str,
    event: LogEvent | str,
    level: LogLevel | str = LogLevel.INFO,
    cycle: int | None = None,
    group_id: str | None = None,
    fields: dict[str, Any] | None = None,
    # legacy compat
    action: str = "",
    reason: str = "",
    delay_used: int | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Build UI + observability log record."""
    std_level = normalize_level(level)
    if isinstance(event, str):
        try:
            log_event = LogEvent(event)
        except ValueError:
            log_event = infer_event_from_legacy(action, reason, message)
    else:
        log_event = event

    merged: dict[str, Any] = dict(fields or {})
    if group_id:
        merged.setdefault("group", group_id)
    if reason:
        merged.setdefault("reason", reason)
    if delay_used is not None:
        merged.setdefault("delay_sec", delay_used)
    if message and log_event == LogEvent.GENERIC:
        merged.setdefault("detail", message.strip())

    now = datetime.now(timezone.utc)
    ts_display = now.astimezone().strftime("%H:%M:%S")
    line = format_log_line(
        timestamp=ts_display,
        level=std_level,
        account_id=account_id,
        event=log_event,
        cycle=cycle,
        fields=merged or None,
    )

    ui_log_level = "success" if str(level).lower() == "success" else ui_level(std_level)

    return {
        "msg": line,
        "level": ui_log_level,
        "time": ts_display,
        "timestamp": now.isoformat(),
        "account_id": account_id,
        "event": log_event.value,
        "level_std": std_level.value,
        "cycle": cycle,
        "group_id": group_id,
        "fields": merged,
        # legacy keys used by dashboard filters
        "action": (action or log_event.value).lower(),
        "reason": reason or "",
        "delay_used": delay_used,
    }


def build_structured_log(
    *,
    account_id: str,
    group_id: str | None,
    action: str,
    reason: str,
    delay_used: int | None,
    level: str,
    message: str,
) -> dict[str, Any]:
    """Backward-compatible wrapper for account_worker._log legacy calls."""
    event = infer_event_from_legacy(action, reason, message)
    fields: dict[str, Any] = {}
    if message and event == LogEvent.GENERIC:
        fields["detail"] = message.strip()
    return build_log_entry(
        account_id=account_id,
        event=event,
        level=level,
        group_id=group_id,
        fields=fields or None,
        action=action,
        reason=reason,
        delay_used=delay_used,
        message=message,
    )


def account_log_line(
    account_id: str,
    event: LogEvent | str,
    level: LogLevel | str = LogLevel.INFO,
    *,
    cycle: int | None = None,
    **fields: Any,
) -> str:
    """Format line and emit to Python logger."""
    entry = build_log_entry(
        account_id=account_id,
        event=event,
        level=level,
        cycle=cycle,
        fields=fields or None,
    )
    py_level = normalize_level(level).value.lower()
    log_fn = getattr(_logger, py_level if py_level != "warn" else "warning", _logger.info)
    log_fn(entry["msg"])
    return entry["msg"]


def log_entry_from_line(account_id: str, line: str, level: str = "info") -> dict[str, Any]:
    """Wrap a pre-formatted structured line (e.g. from features) into a log entry."""
    return build_log_entry(
        account_id=account_id,
        event=LogEvent.GENERIC,
        level=level,
        fields={"detail": line},
        message=line,
    )
