"""Every filter the notifications API accepts must reach the query.

`list_notifications` built a set of "exact" filter names — candidate_id,
candidate_status, priority — and then never read it. The API accepted all three,
the Mail Alerts screen sent them, and the query ignored them, so every request
came back unfiltered.

Nothing failed. A filter that does nothing is indistinguishable from a filter
that matches everything, and the screen showed plausible rows either way. It
surfaced only when the live totals were compared per candidate: five candidates
holding 4, 1, 6, 2 and 4 alerts each returned the same 17 rows.

These tests capture the SQL instead of trusting that a name appearing in the
function means the value is used, and they cover every accepted filter rather
than only the ones a screen happens to send today — the dead set was reachable
from the API long before any UI relied on it.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store


class FakeCursor:
    """Records what was executed; returns a count first, then no rows."""

    def __init__(self):
        self.executed: list[tuple[str, list]] = []
        self.description = []
        self._count_next = True

    def execute(self, sql, params=None):
        self.executed.append((sql, list(params or [])))
        self._count_next = "count(*)" in sql

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def captured(monkeypatch):
    cursor = FakeCursor()
    monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
    return cursor


def where_clause(cursor) -> str:
    return cursor.executed[0][0]


def bound(cursor) -> list:
    return cursor.executed[0][1]


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "cand-1"),
    ("candidate_status", "Offer Received"),
    ("priority", "review_required"),
])
def test_each_exact_filter_reaches_the_query(captured, field, value):
    store.list_notifications(filters={field: value})
    assert f"{field}=%s" in where_clause(captured), f"{field} never reached the SQL"
    assert value in bound(captured), f"{field} value was not bound"


def test_an_absent_filter_adds_no_condition(captured):
    store.list_notifications(filters={})
    assert "candidate_id=%s" not in where_clause(captured)
    assert "priority=%s" not in where_clause(captured)


def test_an_empty_string_is_not_treated_as_a_filter(captured):
    """The screen sends "" for "All candidates". Binding that would match no
    row and empty the table."""
    store.list_notifications(filters={"candidate_id": "", "priority": ""})
    assert "candidate_id=%s" not in where_clause(captured)
    assert "priority=%s" not in where_clause(captured)


def test_the_candidate_and_group_filters_combine(captured):
    store.list_notifications(
        filters={"candidate_id": "cand-1", "classification_group": "interview"}
    )
    clause = where_clause(captured)
    assert "candidate_id=%s" in clause
    assert "classification IN (" in clause
    assert "cand-1" in bound(captured)
    for value in store.INTERVIEW_RELATED_CLASSIFICATIONS:
        assert value in bound(captured)


def test_a_group_filter_restricts_to_that_group_only(captured):
    store.list_notifications(filters={"classification_group": "selection"})
    params = bound(captured)
    for value in store.SELECTION_RELATED_CLASSIFICATIONS:
        assert value in params
    for value in store.INTERVIEW_RELATED_CLASSIFICATIONS:
        assert value not in params


def test_exact_classification_still_wins_over_a_group(captured):
    """Existing callers pass `classification`; that behaviour is unchanged."""
    store.list_notifications(
        filters={"classification": "offer_received", "classification_group": "interview"}
    )
    clause = where_clause(captured)
    assert "classification=%s" in clause
    assert "offer_received" in bound(captured)


def test_an_unknown_group_falls_back_to_tracked_rather_than_nothing(captured):
    """A filter that silently returns nothing reads as "there are no alerts"."""
    store.list_notifications(filters={"classification_group": "nonsense"})
    params = bound(captured)
    for value in store.TRACKED_NOTIFICATION_CLASSIFICATIONS:
        assert value in params


class CountingCursor(FakeCursor):
    """Returns a row wide enough for the summary's column list."""

    def __init__(self):
        super().__init__()

        class Col:
            def __init__(self, name):
                self.name = name

        self.description = [Col(n) for n in ("visible_total", "unread")]

    def fetchone(self):
        return (0, 0)


def test_the_cards_and_the_table_describe_the_same_rows(monkeypatch):
    """The counts above the table used to include rows the table could never
    show: 129 / 116 / 122 over a list of 17 / 4 / 10. Both now build their
    WHERE from the same pieces, so the same constraints must appear in both.
    """
    list_cursor = FakeCursor()
    monkeypatch.setattr(store, "get_connection", lambda: FakeConn(list_cursor))
    store.list_notifications(filters={})
    list_sql = list_cursor.executed[0][0]

    summary_cursor = CountingCursor()
    monkeypatch.setattr(store, "get_connection", lambda: FakeConn(summary_cursor))
    store.notification_summary()
    summary_sql = summary_cursor.executed[0][0]

    rows_clause, _ = store.visible_rows_sql()
    tracked_clause, _ = store.tracked_classification_sql()
    for clause in (rows_clause, tracked_clause):
        assert clause in list_sql, "the table stopped using the shared visibility rule"
        assert clause in summary_sql, "the cards stopped using the shared visibility rule"


def test_the_summary_binds_the_tracked_classifications(monkeypatch):
    cursor = CountingCursor()
    monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
    store.notification_summary()
    params = cursor.executed[0][1]
    for value in store.TRACKED_NOTIFICATION_CLASSIFICATIONS:
        assert value in params


def test_the_shared_rule_is_composed_not_restated():
    """If either half is inlined again the two can drift apart silently."""
    combined, params = store.notification_visibility_sql()
    rows_clause, _ = store.visible_rows_sql()
    tracked_clause, tracked_params = store.tracked_classification_sql()
    assert rows_clause in combined
    assert tracked_clause in combined
    assert params == tracked_params
