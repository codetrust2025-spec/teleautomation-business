from __future__ import annotations

from services.interview_lifecycle import (
    InterviewState,
    LifecycleEvent,
    TransitionDecision,
    decide,
)


def event(*, classification="interview_confirmed", uid="uid-1", sequence=0, sent_at="2026-09-09T10:00:00Z", time="14:00", message_id="m-1"):
    return LifecycleEvent.from_payload(
        "candidate-1",
        {"classification": classification, "calendar": {"uid": uid, "sequence": sequence}, "interview": {"date": "2026-09-11", "time": time, "end_time": "15:00"}},
        {"provider_message_id": message_id, "provider_thread_id": "thread-1", "sent_at": sent_at},
    )


def test_exact_retry_is_idempotent():
    first = event()
    assert decide(first, first) == TransitionDecision.IDEMPOTENT


def test_old_calendar_sequence_cannot_rewind_a_reschedule():
    current = event(classification="interview_rescheduled", sequence=3, time="16:00", message_id="m-3")
    stale = event(sequence=1, time="14:00", message_id="m-1")
    assert decide(current, stale) == TransitionDecision.STOP_STALE


def test_old_confirmation_cannot_resurrect_a_cancelled_interview():
    cancelled = event(classification="interview_cancelled", sequence=2, message_id="cancel")
    old_confirm = event(sequence=1, message_id="old-confirm")
    assert cancelled.state == InterviewState.CANCELLED
    assert decide(cancelled, old_confirm) == TransitionDecision.STOP_STALE


def test_same_revision_conflicting_schedule_is_not_silently_applied():
    previous = event(sequence=2, time="14:00", message_id="first")
    conflicting = event(sequence=2, time="16:00", message_id="second")
    assert decide(previous, conflicting) == TransitionDecision.STOP_CONFLICT


def test_same_revision_cancel_is_a_safe_terminal_transition():
    previous = event(sequence=2, message_id="first")
    cancelled = event(classification="interview_cancelled", sequence=2, message_id="cancel")
    assert decide(previous, cancelled) == TransitionDecision.ALLOW


def test_unidentified_old_mail_cannot_overwrite_a_newer_outcome():
    previous = event(uid="", sent_at="2026-09-09T12:00:00Z", message_id="new")
    old = event(uid="", sent_at="2026-09-09T11:00:00Z", message_id="old")
    assert decide(previous, old) == TransitionDecision.STOP_STALE

