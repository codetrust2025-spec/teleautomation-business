from contextlib import contextmanager

from core.db import candidates_pg
from features.candidate_store import _merge_candidate_snapshot


class _Cursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_pg_save_uses_versioned_rows_instead_of_full_table_rewrite(monkeypatch):
    cursor = _Cursor()

    @contextmanager
    def fake_connection():
        yield _Connection(cursor)

    monkeypatch.setattr(candidates_pg, "get_connection", fake_connection)

    candidates_pg.pg_save(
        {
            "updated_at": "2026-07-27T12:00:00+00:00",
            "_snapshot_versions": {
                "existing": "2026-07-27T11:00:00+00:00",
                "deleted": "2026-07-27T11:00:00+00:00",
            },
            "candidates": [
                {"id": "existing", "name": "Existing"},
                {"id": "new", "name": "New"},
            ],
        }
    )

    statements = [sql for sql, _params in cursor.calls]
    assert any("pg_advisory_xact_lock" in sql for sql in statements)
    assert any(sql.startswith("UPDATE candidates_store") for sql in statements)
    assert any("ON CONFLICT (id) DO NOTHING" in sql for sql in statements)
    assert any(
        sql.startswith("DELETE FROM candidates_store WHERE id =")
        for sql in statements
    )
    assert "DELETE FROM candidates_store" not in statements


def test_json_snapshot_merge_preserves_concurrent_new_row():
    stale_desired = [{"id": "old", "name": "Old", "_store_updated_at": "v1"}]
    current = [
        {"id": "old", "name": "Old", "_store_updated_at": "v1"},
        {"id": "new-payment", "name": "Krishna", "_store_updated_at": "v2"},
    ]

    merged = _merge_candidate_snapshot(
        current,
        stale_desired,
        {"old": "v1"},
        save_at="v3",
    )

    assert {row["id"] for row in merged} == {"old", "new-payment"}


def test_json_snapshot_merge_does_not_overwrite_concurrent_row_update():
    current = [{"id": "krishna", "payment": 5000, "_store_updated_at": "v2"}]
    stale_desired = [{"id": "krishna", "payment": 0, "_store_updated_at": "v1"}]

    merged = _merge_candidate_snapshot(
        current,
        stale_desired,
        {"krishna": "v1"},
        save_at="v3",
    )

    assert merged[0]["payment"] == 5000
    assert merged[0]["_store_updated_at"] == "v2"
