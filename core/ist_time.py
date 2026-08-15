"""Display timestamps in India Standard Time (IST) for UI and exports."""

from __future__ import annotations

from datetime import datetime, time, timezone
import time as _time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
APP_TIMEZONE = "Asia/Kolkata"


def _to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def format_ist_datetime(
    value: datetime | float | int | str | None,
    *,
    with_seconds: bool = False,
    with_year: bool = True,
) -> str:
    """Human-readable IST, e.g. 03 Jun 2026, 01:12 pm."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith(" UTC"):
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        elif text.endswith(" IST"):
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M IST").replace(tzinfo=IST)
        else:
            s = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
    ist = _to_ist(dt)
    fmt = "%d %b"
    if with_year:
        fmt += " %Y"
    fmt += ", %I:%M:%S %p" if with_seconds else ", %I:%M %p"
    return ist.strftime(fmt).lstrip("0").replace(" 0", " ", 1)


def format_ist_storage_label(value: datetime | float | None = None) -> str:
    """Legacy storage label: YYYY-MM-DD HH:MM IST."""
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _to_ist(dt).strftime("%Y-%m-%d %H:%M IST")


def ist_now(now: float | None = None) -> datetime:
    """Current wall-clock time in IST (optional unix override for tests)."""
    if now is not None:
        return datetime.fromtimestamp(float(now), tz=IST)
    return datetime.now(IST)


def ist_date_str(now: float | None = None) -> str:
    """IST calendar date as YYYY-MM-DD."""
    return ist_now(now).strftime("%Y-%m-%d")


def ist_day_start_ts(now: float | None = None) -> float:
    """Unix timestamp for 00:00 IST on the calendar day containing *now*."""
    ts = float(now) if now is not None else _time.time()
    anchor = datetime.fromtimestamp(ts, tz=IST)
    midnight = datetime.combine(anchor.date(), time.min, tzinfo=IST)
    return midnight.timestamp()


def ist_day_start_iso(now: float | None = None) -> str:
    """ISO timestamp for 00:00 IST today (or the day containing *now*)."""
    return format_ist_iso(ist_day_start_ts(now))


def format_ist_iso(value: datetime | float | None = None) -> str:
    """ISO with IST offset for API fields shown in UI."""
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _to_ist(dt).isoformat(timespec="seconds")
