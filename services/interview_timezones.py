"""Resolving the zone a sender wrote, so the schedule can be converted to IST.

Operations run on India time. Every interview is stored and displayed as
Asia/Kolkata, and the only question this module answers is what zone the sender
meant, so the conversion has something to convert *from*.

Three kinds of value are accepted:

* a real IANA name -- ``Asia/Kolkata``, ``America/New_York``
* an abbreviation from the table below
* a numeric offset -- ``+05:30``, ``-0800``, ``UTC+5:30``, ``GMT-8``

Anything else resolves to nothing, which sends the mail to review. Guessing a
zone books a real candidate at the wrong hour, and that is worse than asking.

**Abbreviations map to a place, not to a fixed offset.** ``EST`` becomes
America/New_York rather than tzdata's literal ``EST``, which is a fixed -05:00
with no daylight saving. A sender who writes EST in July means -04:00, so the
literal zone is an hour out for most of the year. Resolving to the place lets
the interview date decide the offset, which is the only way to get summer right.

**CST is the one genuine ambiguity here.** It is US Central (-06:00), China
Standard (+08:00) and Cuba Standard (-05:00), which is a fourteen-hour spread.
It is mapped to America/Chicago because recruitment mail reaching Indian
candidates overwhelmingly comes from US recruiters, but that is a judgement, not
a certainty -- a mail from a China-based recruiter would be booked fourteen hours
out. If that ever bites, the fix is to drop CST from this table so it goes to
review instead.
"""

from __future__ import annotations

import re
from datetime import timedelta, timezone as fixed_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Where operations happen. Every stored booking is expressed in this zone.
OPERATIONS_TIMEZONE = "Asia/Kolkata"

_ABBREVIATIONS = {
    # India, and the older spelling of the same zone.
    "IST": "Asia/Kolkata",
    "ASIA/CALCUTTA": "Asia/Kolkata",
    # Absolute references.
    "UTC": "UTC", "GMT": "UTC", "Z": "UTC", "ZULU": "UTC",
    # United States. Standard and daylight spellings resolve to the same place;
    # the interview date then decides which offset applies.
    "EST": "America/New_York", "EDT": "America/New_York", "ET": "America/New_York",
    "CST": "America/Chicago", "CDT": "America/Chicago", "CT": "America/Chicago",
    "MST": "America/Denver", "MDT": "America/Denver", "MT": "America/Denver",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PT": "America/Los_Angeles",
}

#: "+05:30", "-0800", "+5:30", and the same with a UTC/GMT prefix.
_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?([0-5]\d))?$", re.IGNORECASE)


def _strip_annotation(text: str) -> str:
    """Drop the decoration the model adds -- "IST (Asia/Kolkata)" either way round."""
    bracketed = re.search(r"\(\s*([A-Za-z]+/[A-Za-z0-9_+\-/]+)\s*\)", text)
    if bracketed:
        return bracketed.group(1)
    return re.sub(r"\s*\([^)]*\)\s*", " ", text).strip()


def resolve(raw: Any):
    """The zone the sender meant, or None when it cannot be known.

    Returns a tzinfo usable directly by ``datetime.combine``. Use
    :func:`label` to name it for storage or audit.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    text = _strip_annotation(text)
    if not text:
        return None

    offset = _OFFSET.match(text)
    if offset:
        sign, hours, minutes = offset.group(1), int(offset.group(2)), int(offset.group(3) or 0)
        if hours > 14 or (hours == 14 and minutes):
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        if sign == "-":
            delta = -delta
        total = int(delta.total_seconds())
        name = "UTC%s%02d:%02d" % ("-" if total < 0 else "+", abs(total) // 3600,
                                   (abs(total) % 3600) // 60)
        return fixed_timezone(delta, name)

    named = _ABBREVIATIONS.get(text.upper())
    if named is None and "/" not in text:
        # A bare word that is not in the table. tzdata would accept a few of
        # them as fixed-offset legacy zones; that is exactly the trap above.
        return None
    try:
        return ZoneInfo(named or text)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def label(zone: Any) -> str:
    """Name a resolved zone for storage and audit."""
    key = getattr(zone, "key", None)
    if key:
        return str(key)
    name = zone.tzname(None) if zone is not None else ""
    return str(name or "")


def operations_zone() -> ZoneInfo:
    return ZoneInfo(OPERATIONS_TIMEZONE)
