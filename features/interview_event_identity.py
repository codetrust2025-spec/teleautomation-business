"""When two recruitment mails describe the same interview.

A recruiter books one interview with two mails a minute apart: a covering note
from their own mailbox, and the calendar invitation. Google then attaches the
invitation twice. Left alone that is three chances to create three events for
one meeting, and the existing duplicate checks cannot see it — the two mails
have different subjects and different bodies, which is exactly what
``is_duplicate_content`` now requires before it will call something a repeat.

Two identities are used here, in order of strength:

* the **calendar UID**, which the ICS carries. It is exact: the same UID is the
  same meeting, and ``SEQUENCE`` separates a reschedule from a resend.
* the **schedule**, for the covering note, which has no UID at all. A candidate
  cannot attend two interviews at once, so the same candidate at the same
  date and time is the same interview — but only when the same organisation
  sent both. Two different employers who happen to pick the same slot are two
  interviews, and merging them would lose one.

This module only decides. It reads nothing and writes nothing.
"""
from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _domain(email: Any) -> str:
    text = _clean(email)
    return text.rpartition("@")[2] if "@" in text else ""


def _schedule(row: dict[str, Any]) -> tuple[str, str]:
    return (_clean(row.get("interview_date"))[:10], _clean(row.get("interview_time")))


def same_calendar_meeting(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Same UID, so the same meeting — whatever else differs."""
    left, right = _clean(existing.get("calendar_uid")), _clean(incoming.get("calendar_uid"))
    return bool(left) and left == right


def is_reschedule(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Same meeting, later SEQUENCE: the organiser moved it.

    A reschedule must not be dropped as a duplicate — it carries the new time —
    so it is reported separately from a plain repeat.
    """
    if not same_calendar_meeting(existing, incoming):
        return False
    try:
        return int(incoming.get("calendar_sequence") or 0) > int(existing.get("calendar_sequence") or 0)
    except (TypeError, ValueError):
        return False


def same_scheduled_interview(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """The covering note and the invitation, matched on when and from whom.

    Deliberately conservative: it only applies when one side has no calendar
    identity of its own. Two invitations that both carry a UID are compared by
    UID and nothing else, so two employers at the same hour stay separate.
    """
    if _clean(existing.get("calendar_uid")) and _clean(incoming.get("calendar_uid")):
        return False

    date, time = _schedule(existing)
    if not date or not time or (date, time) != _schedule(incoming):
        return False

    sender = _domain(existing.get("recruiter_email")) or _clean(existing.get("company_domain"))
    other = _domain(incoming.get("recruiter_email")) or _clean(incoming.get("company_domain"))
    return bool(sender) and sender == other


def duplicate_of(existing_rows: list[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any] | None:
    """The already-recorded event this one repeats, or None if it is new.

    A reschedule is never a duplicate: it is the same meeting saying something
    new, and the caller has to apply it.
    """
    for row in existing_rows or []:
        if is_reschedule(row, incoming):
            return None
        if same_calendar_meeting(row, incoming) or same_scheduled_interview(row, incoming):
            return row
    return None
