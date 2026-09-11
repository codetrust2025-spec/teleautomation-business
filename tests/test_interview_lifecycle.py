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


def test_missing_thread_never_makes_time_equality_a_lifecycle_identity():
    from services.interview_lifecycle import interview_key
    value = {'interview': {'date': '2099-09-11', 'time': '12:00', 'end_time': '12:45'}}
    first = interview_key('candidate', value, {'provider_message_id': 'one'})
    assert first != interview_key('candidate', value, {'provider_message_id': 'two'})
    assert first == interview_key('candidate', value, {'provider_message_id': 'one'})


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


def test_equivalent_delivery_after_persisted_booked_state_is_idempotent():
    from dataclasses import replace
    previous = replace(event(), state=InterviewState.BOOKED)
    assert decide(previous, event(message_id='resent')) == TransitionDecision.IDEMPOTENT


def _claim_db(monkeypatch, rows, known=None):
    from types import SimpleNamespace
    from core.db import connection
    from services import recruitment_identity
    class Cursor:
        def __init__(self):
            self.calls = []
            self.description = [SimpleNamespace(name=k) for k in rows[0]]
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=()): self.calls.append((sql, params))
        def fetchall(self): return [tuple(r.values()) for r in rows]
        def fetchone(self): return known
    cursor = Cursor()
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return cursor
    monkeypatch.setattr(connection, 'use_postgres', lambda: True)
    monkeypatch.setattr(connection, 'get_connection', Connection)
    monkeypatch.setattr(recruitment_identity, 'load_links', lambda: {'old-alias': 'person'})
    return cursor


def _state_row(*, state='BOOKED', sequence=0, source='old-key', sent='2026-09-09T10:00:00Z'):
    from services.interview_lifecycle import interview_key
    return dict(interview_key=interview_key('old-alias', {'calendar': {'uid': 'uid-1'}}, {}),
                candidate_id='old-alias', classification='interview_cancelled' if state == 'CANCELLED' else 'interview_confirmed',
                lifecycle_state=state, calendar_uid='uid-1', calendar_sequence=sequence,
                source_message_id='old-message', source_sent_at=sent,
                schedule={'date': '2026-09-11', 'time': '14:00', 'time_end': '15:00'},
                idempotency_key=source, booking_id='existing-slot', transition_status='APPLIED')


def _claim_payload():
    return {'classification': 'interview_confirmed', 'calendar': {'uid': 'uid-1', 'sequence': 0},
            'interview': {'date': '2026-09-11', 'time': '14:00', 'end_time': '15:00'}}


def test_real_claim_recovers_alias_slot_and_transition_without_writes(monkeypatch):
    from services.interview_lifecycle import claim
    row = _state_row()
    cursor = _claim_db(monkeypatch, [row])
    result = claim('person', _claim_payload(), {'provider_message_id': 'resend'})
    assert result.decision == TransitionDecision.IDEMPOTENT
    assert result.booking_id == 'existing-slot'
    assert result.incoming.idempotency_key == 'old-key'
    assert result.key == row['interview_key']
    assert len(cursor.calls) == 1
    assert 'FOR UPDATE' in cursor.calls[0][0]
    assert row['interview_key'] in cursor.calls[0][1][0]


def test_real_claim_does_not_let_old_audit_bypass_alias_cancellation(monkeypatch):
    from services.interview_lifecycle import claim
    cursor = _claim_db(monkeypatch, [_state_row(state='CANCELLED', sequence=1)], known=('old-slot', 'APPLIED'))
    result = claim('person', _claim_payload(), {'provider_message_id': 'old-message'})
    assert result.decision == TransitionDecision.STOP_STALE
    assert len(cursor.calls) == 1  # no historical transition lookup or mutation


def test_alias_cancel_wins_same_sequence_even_if_another_alias_has_later_timestamp(monkeypatch):
    from services.interview_lifecycle import claim
    _claim_db(monkeypatch, [_state_row(state='CANCELLED'), _state_row(sent='2026-09-10T10:00:00Z')])
    assert claim('person', _claim_payload(), {}).decision == TransitionDecision.STOP_STALE


def test_legacy_unthreaded_tombstones_require_same_source_proof(monkeypatch):
    from hashlib import sha256
    from services.interview_lifecycle import claim
    row = _state_row(state='CANCELLED')
    row.update(calendar_uid='', interview_key=sha256('\x1f'.join(
        ('fallback', 'old-alias', '', '2026-09-11', '14:00', '15:00')).encode()).hexdigest())
    cursor = _claim_db(monkeypatch, [row])
    value = _claim_payload(); value['calendar'] = {}
    outcome = claim('person', value, {'provider_message_id': 'old-message', 'sent_at': '2026-09-09T10:00:00Z'})
    assert outcome.decision == TransitionDecision.STOP_STALE
    sql, params = cursor.calls[0]
    assert 'AND source_message_id=%s' in sql
    assert row['interview_key'] in params[1]
    assert params[2] == 'old-message'

