"""Source metadata retains meridiem without rekeying historical lifecycles."""
import json
from contextlib import contextmanager
from hashlib import sha256

import pytest

from core.db import connection
from services import interview_lifecycle as lifecycle, recruitment_identity


@pytest.mark.parametrize('clock', ['03:30 PM', '03:30 AM', '12:00 AM', '12:00 PM'])
@pytest.mark.parametrize('identity', ['calendar', 'fallback', 'message'])
def test_claim_writes_full_clock_but_preserves_existing_keys(monkeypatch, clock, identity):
    result = {'classification': 'interview_confirmed', 'interview': {
        'date': '2026-09-16', 'time': clock, 'end_time': '04:00 PM', 'timezone': 'Asia/Kolkata',
    }}
    message = {'provider_message_id': 'mail'}
    if identity == 'calendar':
        result['calendar'] = {'uid': 'meeting-uid', 'sequence': 2}
        material = ('calendar', 'person', 'meeting-uid')
    elif identity == 'fallback':
        message['provider_thread_id'] = 'thread'
        material = ('fallback', 'person', 'thread', '2026-09-16', clock[:5], '04:00')
    else:
        material = ('message', 'person', 'mail', '2026-09-16', clock[:5], '04:00')
    old_key = sha256('\x1f'.join(material).encode()).hexdigest()
    old_attempt = sha256('\x1f'.join((old_key, 'interview_confirmed',
        '2' if identity == 'calendar' else '0', 'mail', '2026-09-16', clock[:5], '04:00')).encode()).hexdigest()
    calls = []
    class Cursor:
        description = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=()): calls.append((sql, params))
        def fetchall(self): return []
        def fetchone(self): return None
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connect(): yield Connection()
    monkeypatch.setattr(connection, 'get_connection', connect)
    monkeypatch.setattr(connection, 'use_postgres', lambda: True)
    monkeypatch.setattr(recruitment_identity, 'load_links', lambda: {})
    claimed = lifecycle.claim('person', result, message)
    assert claimed.key == old_key
    assert claimed.incoming.idempotency_key == old_attempt
    state_insert, transition_insert = calls[-2:]
    assert 'INSERT INTO interview_lifecycle_states' in state_insert[0]
    assert 'INSERT INTO interview_lifecycle_transitions' in transition_insert[0]
    for raw in (state_insert[1][8], transition_insert[1][6]):
        assert json.loads(raw) == {'date': '2026-09-16', 'time': clock,
                                  'time_end': '04:00 PM', 'timezone': 'Asia/Kolkata'}
    # Both older truncated rows and new full-source rows deserialize to the
    # same identity comparison. No booking, tombstone or key migration needed.
    for stored_clock in (clock[:5], clock):
        previous = lifecycle._from_row({
            'candidate_id': 'person', 'classification': 'interview_confirmed',
            'lifecycle_state': 'BOOKED', 'idempotency_key': old_attempt,
            'schedule': {'date': '2026-09-16', 'time': stored_clock, 'time_end': '04:00 PM'},
        })
        assert previous.schedule == claimed.incoming.schedule
        assert lifecycle.decide(previous, claimed.incoming) == lifecycle.TransitionDecision.IDEMPOTENT


def test_time_end_alias_and_empty_cancellation_metadata():
    assert lifecycle.schedule_metadata({'interview': {'time_end': '04:00 PM'}})['time_end'] == '04:00 PM'
    assert lifecycle.schedule_metadata({}) == {'date': '', 'time': '', 'time_end': '', 'timezone': ''}
