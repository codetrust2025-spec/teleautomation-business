"""Real PostgreSQL audit/migration tests, required in CI; never production."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store


@pytest.fixture
def audit_db(monkeypatch):
    url = os.getenv('AUDIT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Dedicated PostgreSQL audit tests run in CI')
    target = urlparse(url)
    assert target.hostname in {'localhost', '127.0.0.1'} and target.path == '/business_ci'
    import psycopg2
    from psycopg2 import sql
    schema = 'audit_test_' + uuid.uuid4().hex
    @contextmanager
    def connect():
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    root = Path(__file__).resolve().parents[1] / 'core' / 'migrations'
    ddl = (root / '008_recruitment_mail_auto_booking.sql').read_text()
    ddl = ddl[ddl.index('CREATE TABLE IF NOT EXISTS interview_auto_booking_audit'):]
    ddl = ddl[:ddl.index('\n);') + 3]
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute('CREATE TABLE interview_mail_analyses(id text PRIMARY KEY)')
            cur.execute("INSERT INTO interview_mail_analyses VALUES ('ia1'),('ia2')")
            cur.execute(ddl)
            cur.execute("""INSERT INTO interview_auto_booking_audit
              (id,booking_id,gmail_message_id,candidate_id,classification,auto_booked,validation_status,booking_status)
              VALUES('legacy','legacy-slot','legacy-message','old-alias','interview_confirmed',true,'PASSED','Auto Booked')""")
            cur.execute('SELECT to_jsonb(a) FROM interview_auto_booking_audit a')
            before = cur.fetchone()[0]
            cur.execute((root / '035_append_only_booking_audit.sql').read_text())
            cur.execute("SELECT to_jsonb(a)-'audit_fact_key'-'source_event_id'-'lifecycle_transition_key'-'source_snapshot' FROM interview_auto_booking_audit a")
            assert cur.fetchone()[0] == before
        monkeypatch.setattr(store, 'get_connection', connect)
        yield connect
    finally:
        # Only this test's generated schema in the dedicated localhost CI DB.
        assert schema.startswith('audit_test_') and len(schema) == 43
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def write(**overrides):
    values = dict(analysis_id='ia1', candidate_id='person', gmail_message_id='mail',
        gmail_thread_id='thread', classification='interview_confirmed', booking_id='slot',
        auto_booked=True, validation_status='PASSED', payment_status='PASSED',
        duplicate_status='PASSED', conflict_status='NOT_REQUIRED', booking_status='Auto Booked',
        source_event_id='event', lifecycle_transition_key='transition',
        source_snapshot={'result': {'calendar_uid': 'uid', 'calendar_sequence': 1}},
        new_booking={'id': 'slot', 'date': '2099-07-20', 'time': '15:00'})
    return store.record_booking_audit(**(values | overrides))


def test_legacy_retry_never_backfills_or_repoints_history(audit_db):
    with audit_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM interview_auto_booking_audit WHERE id='legacy'")
        before = store._rows(cur)[0]
    retried = write(gmail_message_id='legacy-message', booking_id='legacy-slot', candidate_id='new-canonical')
    assert retried == before


def test_historical_replay_appends_without_erasing_original(audit_db):
    original = write()
    replay = write(booking_id=None, auto_booked=False, booking_status='Historical Skipped', failure_code='PAST_INTERVIEW')
    assert original['id'] != replay['id']
    assert store.booking_audit_for_message('mail', 'interview_confirmed') == original
    assert write(candidate_id='new-alias', analysis_id='ia2', source_event_id='new-event', correlation_id='retry') == original
    assert original['source_event_id'] == 'event' and original['email_analysis_id'] == 'ia1'


@pytest.mark.parametrize('transition', ['rescheduled', 'cancelled'])
def test_later_transition_and_old_replay_preserve_original_references(audit_db, transition):
    original = write()
    changed = write(gmail_message_id='change', classification='interview_' + transition,
        booking_status=transition.title(), source_event_id='new-event', lifecycle_transition_key='new-transition',
        previous_booking=original['new_booking'], new_booking={'id': 'slot', 'changed': transition})
    replay = write(auto_booked=False, booking_id=None, booking_status='Blocked', failure_code='STALE_INTERVIEW_EVENT')
    assert len({original['id'], changed['id'], replay['id']}) == 3
    assert store.booking_audit_for_message('mail', 'interview_confirmed') == original
    assert changed['previous_booking'] == original['new_booking']


def test_concurrent_duplicate_and_alias_retries_return_one_fact(audit_db):
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda i: write(candidate_id=f'alias-{i}', correlation_id=str(i)), range(16)))
    assert len({r['id'] for r in rows}) == 1
    assert all(r == rows[0] for r in rows)


def test_crash_after_commit_retry_returns_same_fact_and_success_survives_failure(audit_db):
    original = write()
    failed = write(auto_booked=False, booking_id=None, booking_status='Processing Failed', failure_code='ConnectionError')
    assert write(correlation_id='retry-after-crash') == original
    assert write(auto_booked=False, booking_id=None, booking_status='Processing Failed', failure_code='ConnectionError') == failed
    assert store.booking_audit_for_message('mail', 'interview_confirmed') == original


def test_crash_before_commit_rolls_back_and_retry_commits_once(audit_db, monkeypatch):
    @contextmanager
    def interrupted():
        with audit_db() as conn:
            yield conn
            raise RuntimeError('simulated crash before commit')
    monkeypatch.setattr(store, 'get_connection', interrupted)
    with pytest.raises(RuntimeError):
        write()
    monkeypatch.setattr(store, 'get_connection', audit_db)
    committed = write()
    assert write() == committed
    with audit_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM interview_auto_booking_audit WHERE gmail_message_id='mail'")
        assert cur.fetchone()[0] == 1


@pytest.mark.parametrize('mutation', [
    "UPDATE interview_auto_booking_audit SET booking_id='other' WHERE id='legacy'",
    "DELETE FROM interview_auto_booking_audit WHERE id='legacy'",
    'TRUNCATE interview_auto_booking_audit',
])
def test_database_rejects_mutation(audit_db, mutation):
    import psycopg2
    with pytest.raises(psycopg2.Error, match='append-only'):
        with audit_db() as conn, conn.cursor() as cur:
            cur.execute(mutation)


def test_database_rejects_success_without_booking_reference(audit_db):
    import psycopg2
    with pytest.raises(psycopg2.Error, match='booking reference'):
        write(booking_id=None)


def test_database_rejects_keyless_new_facts(audit_db):
    import psycopg2
    with pytest.raises(psycopg2.Error, match='idempotency key'):
        with audit_db() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO interview_auto_booking_audit
              (id,gmail_message_id,candidate_id,classification,validation_status,booking_status)
              VALUES('keyless','other','person','interview_confirmed','FAILED','Processing Failed')""")


@pytest.mark.parametrize('state', ['CANCELLED', 'RESCHEDULED'])
def test_real_booking_entry_replay_cannot_repoint_original_audit_or_alert(audit_db, monkeypatch, state):
    from tests.test_interview_auto_booking import install_store_fakes, result, execute, slot_writer
    from services import interview_auto_booking as booking, interview_lifecycle as lifecycle
    real_writer, real_reader = store.record_booking_audit, store.booking_audit_for_message
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(store, 'record_booking_audit', real_writer)
    monkeypatch.setattr(store, 'booking_audit_for_message', real_reader)
    monkeypatch.setattr(store, 'canonical_candidate_id', lambda value: value)
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', slot_writer('slot1'))
    original = execute(result())
    assert original['status'] == 'Auto Booked'
    old_audit = deepcopy(original['audit'])
    incoming = lifecycle.LifecycleEvent.from_payload('c1', result(), {'provider_message_id': 'gm1'})
    previous = lifecycle.LifecycleEvent.from_payload('c1', result('interview_' + state.lower()),
        {'provider_message_id': 'later', 'sent_at': '2099-07-21T00:00:00Z'}, state=lifecycle.InterviewState(state))
    # Real precedence decision, with explicit source ordering.
    from dataclasses import replace
    incoming = replace(incoming, sent_at=lifecycle._sent_at('2099-07-19T00:00:00Z'))
    decision = lifecycle.decide(previous, incoming)
    assert decision == lifecycle.TransitionDecision.STOP_STALE
    monkeypatch.setattr(lifecycle, 'claim', lambda *args: lifecycle.LifecycleClaim(decision, incoming, 'key'))
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', lambda **kw: pytest.fail('old replay booked'))
    monkeypatch.setattr(store, 'attach_booking_to_notification', lambda *a, **kw: pytest.fail('old replay repointed alert'))
    replay = execute(result())
    assert replay['failure_code'] == 'STALE_INTERVIEW_EVENT'
    assert store.booking_audit_for_message('gm1', 'interview_confirmed') == old_audit
    assert replay['audit']['id'] != old_audit['id']
