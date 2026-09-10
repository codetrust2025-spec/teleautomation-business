"""Pure, conservative lifecycle rules for one interview identity.

This module intentionally does not write to the candidate store.  It is the
single decision point that a booking/replay worker must consult *before* it
changes a slot.  Keeping the graph pure makes both replay and crash recovery
testable without turning an old email into a new booking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
import json
import uuid


class InterviewState(str, Enum):
    DETECTED = "DETECTED"
    VALIDATING = "VALIDATING"
    READY_TO_BOOK = "READY_TO_BOOK"
    BOOKED = "BOOKED"
    RESCHEDULED = "RESCHEDULED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class TransitionDecision(str, Enum):
    ALLOW = "ALLOW"
    IDEMPOTENT = "IDEMPOTENT"
    STOP_STALE = "STOP_STALE"
    STOP_CONFLICT = "STOP_CONFLICT"
    STOP_NEEDS_REVIEW = "STOP_NEEDS_REVIEW"


ACTIONABLE = frozenset({"interview_confirmed", "interview_rescheduled", "interview_cancelled"})
TERMINAL = frozenset({InterviewState.CANCELLED, InterviewState.BLOCKED, InterviewState.NEEDS_REVIEW})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _classification(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def _calendar(result: Mapping[str, Any]) -> Mapping[str, Any]:
    calendar = result.get("calendar") or {}
    return calendar if isinstance(calendar, Mapping) else {}


def calendar_uid(result: Mapping[str, Any]) -> str:
    return _text(_calendar(result).get("uid") or result.get("calendar_uid")).casefold()


def calendar_sequence(result: Mapping[str, Any]) -> int:
    try:
        return max(0, int(_calendar(result).get("sequence") or result.get("calendar_sequence") or 0))
    except (TypeError, ValueError):
        return 0


def _schedule(result: Mapping[str, Any]) -> tuple[str, str, str]:
    interview = result.get("interview") or {}
    if not isinstance(interview, Mapping):
        interview = {}
    return (
        _text(interview.get("date"))[:10],
        _text(interview.get("time"))[:5],
        _text(interview.get("end_time") or interview.get("time_end"))[:5],
    )


def interview_key(candidate_id: str, result: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    """Stable key for a lifecycle row.

    A calendar UID is authoritative.  Without one, do not pretend two vague
    mails are identical: scope the fallback to candidate, thread and schedule.
    """
    candidate = _text(candidate_id)
    uid = calendar_uid(result)
    if uid:
        material = ("calendar", candidate, uid)
    else:
        material = ("fallback", candidate, _text(message.get("provider_thread_id")), *_schedule(result))
    return sha256("\x1f".join(material).encode("utf-8")).hexdigest()


def idempotency_key(candidate_id: str, result: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    """Key one attempted state transition, not merely one Gmail message."""
    material = (
        interview_key(candidate_id, result, message),
        _classification(result.get("classification") or result.get("primary_status")),
        str(calendar_sequence(result)),
        _text(message.get("provider_message_id")),
        *_schedule(result),
    )
    return sha256("\x1f".join(material).encode("utf-8")).hexdigest()


def _sent_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class LifecycleEvent:
    candidate_id: str
    classification: str
    state: InterviewState
    calendar_uid: str
    calendar_sequence: int
    source_message_id: str
    sent_at: datetime | None
    schedule: tuple[str, str, str]
    idempotency_key: str

    @classmethod
    def from_payload(
        cls, candidate_id: str, result: Mapping[str, Any], message: Mapping[str, Any], *, state: InterviewState | None = None,
    ) -> "LifecycleEvent":
        classification = _classification(result.get("classification") or result.get("primary_status"))
        inferred = {
            "interview_confirmed": InterviewState.READY_TO_BOOK,
            "interview_rescheduled": InterviewState.RESCHEDULED,
            "interview_cancelled": InterviewState.CANCELLED,
        }.get(classification, InterviewState.NEEDS_REVIEW)
        return cls(
            candidate_id=_text(candidate_id), classification=classification, state=state or inferred,
            calendar_uid=calendar_uid(result), calendar_sequence=calendar_sequence(result),
            source_message_id=_text(message.get("provider_message_id")), sent_at=_sent_at(message.get("sent_at")),
            schedule=_schedule(result), idempotency_key=idempotency_key(candidate_id, result, message),
        )


def decide(previous: LifecycleEvent | None, incoming: LifecycleEvent) -> TransitionDecision:
    """Return the only safe replay decision for one logical interview.

    Calendar sequence wins whenever it exists.  Without a UID/sequence, an
    older sent timestamp cannot overwrite a newer lifecycle outcome.  Equal
    versions are only idempotent if they are the same transition; a cancelled
    event is never revived by an equal/older confirmation or reschedule.
    """
    if incoming.classification not in ACTIONABLE:
        return TransitionDecision.STOP_NEEDS_REVIEW
    if previous is None:
        return TransitionDecision.ALLOW
    if incoming.idempotency_key == previous.idempotency_key:
        return TransitionDecision.IDEMPOTENT
    if previous.calendar_uid and incoming.calendar_uid and previous.calendar_uid == incoming.calendar_uid:
        if incoming.calendar_sequence < previous.calendar_sequence:
            return TransitionDecision.STOP_STALE
        if incoming.calendar_sequence == previous.calendar_sequence:
            if previous.state == InterviewState.CANCELLED:
                return TransitionDecision.STOP_STALE
            if incoming.state == previous.state and incoming.schedule == previous.schedule:
                return TransitionDecision.IDEMPOTENT
            # A cancellation at the same sequence is a monotonic terminal
            # transition; a conflicting confirmation/reschedule is not.
            if incoming.state == InterviewState.CANCELLED:
                return TransitionDecision.ALLOW
            return TransitionDecision.STOP_CONFLICT
        return TransitionDecision.ALLOW
    if previous.sent_at and incoming.sent_at and incoming.sent_at < previous.sent_at:
        return TransitionDecision.STOP_STALE
    if previous.state == InterviewState.CANCELLED and incoming.sent_at == previous.sent_at:
        return TransitionDecision.STOP_STALE
    return TransitionDecision.ALLOW


@dataclass(frozen=True)
class LifecycleClaim:
    decision: TransitionDecision
    incoming: LifecycleEvent
    key: str
    booking_id: str = ""
    transition_status: str = "PENDING"


def _from_row(row: Mapping[str, Any]) -> LifecycleEvent:
    schedule = row.get("schedule") or {}
    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except json.JSONDecodeError:
            schedule = {}
    return LifecycleEvent(
        candidate_id=_text(row.get("candidate_id")), classification=_text(row.get("classification")),
        state=InterviewState(_text(row.get("lifecycle_state")) or InterviewState.NEEDS_REVIEW),
        calendar_uid=_text(row.get("calendar_uid")).casefold(), calendar_sequence=int(row.get("calendar_sequence") or 0),
        source_message_id=_text(row.get("source_message_id")), sent_at=_sent_at(row.get("source_sent_at")),
        schedule=(_text(schedule.get("date"))[:10], _text(schedule.get("time"))[:5], _text(schedule.get("time_end"))[:5]),
        idempotency_key=_text(row.get("idempotency_key")),
    )


def claim(candidate_id: str, result: Mapping[str, Any], message: Mapping[str, Any]) -> LifecycleClaim:
    """Persist a guarded intent before a booking side effect.

    The caller already holds the candidate advisory lock.  The row lock below
    adds protection for workers that converge on the same calendar identity.
    In non-Postgres local tests there is no durable store, so the guard is a
    no-op; production always uses the transaction path.
    """
    incoming = LifecycleEvent.from_payload(candidate_id, result, message)
    key = interview_key(candidate_id, result, message)
    from core.db.connection import get_connection, use_postgres
    if not use_postgres():
        return LifecycleClaim(TransitionDecision.ALLOW, incoming, key)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM interview_lifecycle_states WHERE interview_key=%s FOR UPDATE", (key,))
        names = [column.name for column in cur.description]
        row = cur.fetchone()
        previous = _from_row(dict(zip(names, row))) if row else None
        decision = decide(previous, incoming)
        cur.execute("SELECT booking_id,transition_status FROM interview_lifecycle_transitions WHERE idempotency_key=%s", (incoming.idempotency_key,))
        known = cur.fetchone()
        if known:
            return LifecycleClaim(TransitionDecision.IDEMPOTENT, incoming, key, _text(known[0]), _text(known[1]))
        if decision != TransitionDecision.ALLOW:
            return LifecycleClaim(decision, incoming, key)
        schedule = {"date": incoming.schedule[0], "time": incoming.schedule[1], "time_end": incoming.schedule[2]}
        cur.execute(
            """INSERT INTO interview_lifecycle_states(interview_key,candidate_id,calendar_uid,calendar_sequence,source_message_id,source_sent_at,classification,lifecycle_state,schedule,idempotency_key,transition_status)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'PENDING')
               ON CONFLICT(interview_key) DO UPDATE SET candidate_id=EXCLUDED.candidate_id,calendar_uid=EXCLUDED.calendar_uid,calendar_sequence=EXCLUDED.calendar_sequence,source_message_id=EXCLUDED.source_message_id,source_sent_at=EXCLUDED.source_sent_at,classification=EXCLUDED.classification,lifecycle_state=EXCLUDED.lifecycle_state,schedule=EXCLUDED.schedule,idempotency_key=EXCLUDED.idempotency_key,transition_status='PENDING',updated_at=now()""",
            (key, incoming.candidate_id, incoming.calendar_uid or None, incoming.calendar_sequence, incoming.source_message_id, incoming.sent_at, incoming.classification, incoming.state.value, json.dumps(schedule), incoming.idempotency_key),
        )
        cur.execute(
            """INSERT INTO interview_lifecycle_transitions(id,interview_key,idempotency_key,source_message_id,classification,lifecycle_state,schedule,transition_status)
               VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,'PENDING')""",
            (str(uuid.uuid4()), key, incoming.idempotency_key, incoming.source_message_id, incoming.classification, incoming.state.value, json.dumps(schedule)),
        )
    return LifecycleClaim(TransitionDecision.ALLOW, incoming, key)


def mark_applied(claimed: LifecycleClaim, *, booking_id: str, state: InterviewState) -> None:
    """Mark a transition only after candidate storage has been re-verified."""
    from core.db.connection import get_connection, use_postgres
    if not use_postgres():
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE interview_lifecycle_transitions SET booking_id=%s,transition_status='APPLIED',applied_at=now() WHERE idempotency_key=%s", (booking_id or None, claimed.incoming.idempotency_key))
        cur.execute("UPDATE interview_lifecycle_states SET booking_id=%s,lifecycle_state=%s,transition_status='APPLIED',updated_at=now() WHERE interview_key=%s", (booking_id or None, state.value, claimed.key))
