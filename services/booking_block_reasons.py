"""Why an automatic booking was blocked, in words an operator can act on.

The booking validator raises precise internal codes — INVALID_TIMEZONE,
CROSS_DAY_INTERVIEW, BOOKING_AMBIGUOUS — which are the right level of detail
for a log and the wrong level for a notification row. Someone looking at Mail
Monitoring wants to know whether to re-send the invite, pick a slot by hand, or
chase the candidate.

So each internal code maps to two things: a stable reason code for anything
that has to branch on the outcome, and a sentence for the person reading the
table. The internal code is never discarded — it is stored alongside, because
it is what identifies the exact validator branch when something needs
debugging.

The mapping lives here rather than in the frontend so that the reason shown to
a user is the reason the backend actually decided, not a guess reconstructed
from a status string.
"""

from __future__ import annotations

from typing import Any

# Reason codes. Deliberately coarser than the validator's internal codes: these
# describe what an operator has to do about it.
DUPLICATE_BOOKING = "DUPLICATE_BOOKING"
MISSING_DATE_TIME = "MISSING_DATE_TIME"
PAST_INTERVIEW_DATE = "PAST_INTERVIEW_DATE"
NO_MATCHING_SLOT = "NO_MATCHING_SLOT"
CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
ROUND_NOT_FOUND = "ROUND_NOT_FOUND"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
DUPLICATE_INVITE = "DUPLICATE_INVITE"
PAYMENT_NOT_CLEARED = "PAYMENT_NOT_CLEARED"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
BOOKING_NOT_SAVED = "BOOKING_NOT_SAVED"

REASON_TEXT = {
    DUPLICATE_BOOKING: "Candidate already has a booking for this round",
    MISSING_DATE_TIME: "Interview date or time could not be detected",
    PAST_INTERVIEW_DATE: "Interview date is in the past",
    NO_MATCHING_SLOT: "No available slot matches the invite time",
    CANDIDATE_NOT_FOUND: "Candidate could not be identified",
    ROUND_NOT_FOUND: "Interview round could not be identified",
    LOW_CONFIDENCE: "AI confidence is below the required threshold",
    INCOMPLETE_EVIDENCE: "Invite screenshot or email details are incomplete",
    DUPLICATE_INVITE: "Duplicate invite detected",
    PAYMENT_NOT_CLEARED: "Payment is not cleared for this interview",
    MANUAL_REVIEW_REQUIRED: "Booking requires manual review",
    BOOKING_NOT_SAVED: "Booking was not saved — book this slot manually and report it",
}

# Validator code -> reason code. Anything absent falls back to manual review,
# which is the safe answer: a block nobody has classified still needs a human.
_INTERNAL_TO_REASON = {
    "DUPLICATE_BOOKING": DUPLICATE_BOOKING,
    "SLOT_CONFLICT": NO_MATCHING_SLOT,
    # Every way the schedule can fail to parse reads the same to an operator:
    # the invite did not yield a usable date and time.
    "INVALID_DATE": MISSING_DATE_TIME,
    "INVALID_TIME": MISSING_DATE_TIME,
    "INVALID_END_TIME": MISSING_DATE_TIME,
    "INVALID_DURATION": MISSING_DATE_TIME,
    "MISSING_TIMEZONE": MISSING_DATE_TIME,
    "INVALID_TIMEZONE": MISSING_DATE_TIME,
    "CROSS_DAY_INTERVIEW": MISSING_DATE_TIME,
    "MEDIUM_CONFIDENCE_INCOMPLETE": MISSING_DATE_TIME,
    "INCOMPLETE_SCHEDULE": MISSING_DATE_TIME,
    "HISTORICAL_SCHEDULE_INCOMPLETE": MISSING_DATE_TIME,
    "PAST_INTERVIEW": PAST_INTERVIEW_DATE,
    "CANDIDATE_MAPPING_FAILED": CANDIDATE_NOT_FOUND,
    "BOOKING_NOT_FOUND": ROUND_NOT_FOUND,
    "BOOKING_AMBIGUOUS": ROUND_NOT_FOUND,
    "LOW_CONFIDENCE": LOW_CONFIDENCE,
    "MISSING_EVIDENCE": INCOMPLETE_EVIDENCE,
    "AI_NOT_VALIDATED": INCOMPLETE_EVIDENCE,
    "PAYMENT_VALIDATION_FAILED": PAYMENT_NOT_CLEARED,
    "AI_REQUIRES_REVIEW": MANUAL_REVIEW_REQUIRED,
    "AUTO_BOOKING_DISABLED": MANUAL_REVIEW_REQUIRED,
    "NOT_ACTIONABLE": MANUAL_REVIEW_REQUIRED,
    # The store accepted the write and the row does not hold the slot. This is
    # never the invite's fault, so it must not read as a parsing or duplicate
    # problem — it is a storage failure an operator has to act on.
    "BOOKING_NOT_PERSISTED": BOOKING_NOT_SAVED,
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def reason_code_for(internal_code: Any) -> str:
    return _INTERNAL_TO_REASON.get(str(internal_code or "").strip().upper(), MANUAL_REVIEW_REQUIRED)


def format_schedule(date: Any, time: Any = None) -> str:
    """'2026-08-03' + '16:30' -> '3 Aug 2026, 4:30 PM'.

    Returns "" when there is nothing usable, so callers can leave the reason
    unqualified rather than printing a half-formed date.
    """
    text = str(date or "").strip()[:10]
    parts = text.split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return ""
    year, month, day = (int(part) for part in parts)
    if not 1 <= month <= 12:
        return ""
    stamp = f"{day} {_MONTHS[month - 1]} {year}"

    # A normalized schedule stores 24-hour "16:30"; the raw AI extraction uses
    # the contract's 12-hour "4:30 PM". Either can reach here depending on how
    # far validation got before it failed.
    clock = str(time or "").strip().upper()
    suffix = ""
    for meridiem in ("AM", "PM"):
        if clock.endswith(meridiem):
            suffix, clock = meridiem, clock[: -len(meridiem)].strip()
            break
    hhmm = clock.split(":")
    if len(hhmm) != 2 or not all(part.strip().isdigit() for part in hhmm):
        return stamp
    hour, minute = int(hhmm[0]), int(hhmm[1])
    if suffix:
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            return stamp
        return f"{stamp}, {hour}:{minute:02d} {suffix}"
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return stamp
    return f"{stamp}, {hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def describe(
    internal_code: Any,
    *,
    schedule: dict[str, Any] | None = None,
    interview: dict[str, Any] | None = None,
) -> dict[str, str]:
    """The blocking reason as the notification should carry it.

    `schedule` is the normalized booking schedule when the validator got far
    enough to build one; `interview` is the raw AI extraction, used when it did
    not. Naming the time the invite asked for is the difference between "no
    available slot matches" and a reason an operator can act on immediately.
    """
    code = str(internal_code or "").strip().upper()
    reason_code = reason_code_for(code)
    text = REASON_TEXT[reason_code]

    source = schedule or interview or {}
    when = format_schedule(source.get("date"), source.get("time"))
    if when and reason_code in {NO_MATCHING_SLOT, DUPLICATE_BOOKING, PAST_INTERVIEW_DATE}:
        text = f"{text} ({when})"
    return {
        "reason_code": reason_code,
        "reason": text,
        # The exact validator branch, kept for debugging and for anything that
        # needs to distinguish causes this mapping deliberately merges.
        "internal_code": code,
    }
