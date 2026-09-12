"""Startup must not replay data migrations after their checksum is recorded."""
from contextlib import contextmanager

from core import recruitment_mail_store as store
from core.migrations import runner


def test_all_schema_entry_calls_use_the_ledger(monkeypatch, tmp_path):
    migration = tmp_path / '001_recruitment_mail_test.sql'
    migration.write_text('UPDATE operational_flags SET pending=false;', encoding='utf-8')
    ledger, effects = {}, []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None):
            self.answer = None
            if sql.startswith('SELECT checksum'):
                self.answer = (ledger[params[0]],) if params[0] in ledger else None
            elif sql.startswith('INSERT INTO operations_schema_migrations'):
                ledger[params[0]] = params[1]
            elif sql.startswith('UPDATE operational_flags'):
                effects.append(sql)
        def fetchone(self): return self.answer
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connect(): yield Connection()
    monkeypatch.setattr(runner, 'MIGRATIONS_DIR', tmp_path)
    monkeypatch.setattr(runner, 'get_connection', connect)
    monkeypatch.setattr(runner, 'use_postgres', lambda: True)
    monkeypatch.setattr(store, 'use_postgres', lambda: True)
    def untracked_connection():
        raise AssertionError('Schema entry point bypassed the checksum ledger')
    monkeypatch.setattr(store, 'get_connection', untracked_connection)
    store.ensure_schema()  # route installation
    runner.apply_migrations()  # main startup
    store.ensure_schema()  # legacy main startup call
    assert len(effects) == 1
    assert list(ledger) == [migration.name]


def test_flag_cleanup_does_not_rewrite_booking_or_model_history():
    sql = (runner.MIGRATIONS_DIR / '034_retire_replayed_review_flags.sql').read_text()
    assert 'previous_value' in sql and 'reviewed_at IS NULL' in sql
    assert "review_status NOT IN ('FALSE_POSITIVE','FALSE_POS','REJECTED','IGNORED')" in sql
    update = sql.split('UPDATE ai_recruitment_events e')[1]
    assert 'classification=' not in update and 'primary_status=' not in update
    assert 'structured_result=' not in update and 'interview_time=' not in update
    assert 'UPDATE mailbox_messages' not in sql and 'UPDATE candidates' not in sql
