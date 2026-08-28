"""Rescanning a month must recover missed alerts without inventing news.

Three things are easy to get wrong here in a direction that looks like success:

* Counting alerts that already existed as alerts the run recovered. A rescan
  re-reads every mail in the range, so most of what it finds is not news.
* Declaring the range finished when the Gmail jobs go green. Ingestion defers
  classification to a durable AI queue, so at that moment the month may be
  stored and entirely unread.
* Reporting created alerts without checking any of them made a sound.

Each is asserted here against the behaviour rather than the intention.
"""

from __future__ import annotations

from datetime import date

import pytest

from core import recruitment_mail_store as store
from services import bulk_mail_rescan, selection_rescan


class FakeCursor:
    """Answers `create_monitoring_notification`'s three queries in order."""

    def __init__(self, *, alert_exists: bool):
        self.alert_exists = alert_exists
        self.executed: list[tuple[str, list]] = []
        self._mode = ""

    def execute(self, sql, params=None):
        self.executed.append((sql, list(params or [])))
        if "FROM mailbox_messages m WHERE m.id" in sql:
            self._mode = "source"
        elif "SELECT id FROM mail_monitoring_notifications" in sql:
            self._mode = "existing"
        else:
            self._mode = "insert"

    @property
    def description(self):
        class Column:
            def __init__(self, name):
                self.name = name
        return [Column(name) for name in ("id", "candidate_id", "classification", "gmail_message_id")]

    def fetchone(self):
        if self._mode == "source":
            return ("gmail-1", "thread-1", "Offer letter", "HR", "hr@company.test",
                    "2026-08-04T09:00:00Z", "mailbox-1", "candidate@mail.test")
        if self._mode == "existing":
            return ("notification-existing",) if self.alert_exists else None
        return None

    def fetchall(self):
        return [("notification-1", "candidate-1", "offer_received", "gmail-1")]

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


def _install_cursor(monkeypatch, cursor):
    from contextlib import contextmanager

    @contextmanager
    def connection():
        yield FakeConn(cursor)

    monkeypatch.setattr(store, "get_connection", connection)


def _alert_event():
    return {
        "id": "event-1",
        "candidate_id": "candidate-1",
        "mailbox_message_id": "message-1",
        "primary_status": "OFFER_LETTER_RECEIVED",
        "confidence": 0.95,
        "structured_result": {
            "evidence": [{"source": "EMAIL_BODY", "meaning": "OFFER_LETTER_RECEIVED", "text": "offer"}],
            "company": {"name": "Test Company"},
            "job": {"title": "Engineer"},
        },
        "requires_manual_review": False,
        "validation_status": "AUTO_VALIDATED",
    }


def test_a_first_time_alert_reports_itself_as_new(monkeypatch):
    cursor = FakeCursor(alert_exists=False)
    _install_cursor(monkeypatch, cursor)
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *_: ("Asha Rao", "asha@mail.test"))

    row = store.create_monitoring_notification(
        _alert_event(), {"id": "analysis-1", "classification": "offer_received", "candidate_status": "Offer Received"},
    )
    assert row["is_new_alert"] is True


def test_an_alert_that_already_exists_is_not_reported_as_new(monkeypatch):
    """The upsert returns a row either way, so the question has to be asked
    before it runs -- afterwards there is nothing left to distinguish them."""
    cursor = FakeCursor(alert_exists=True)
    _install_cursor(monkeypatch, cursor)
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *_: ("Asha Rao", "asha@mail.test"))

    row = store.create_monitoring_notification(
        _alert_event(), {"id": "analysis-1", "classification": "offer_received", "candidate_status": "Offer Received"},
    )
    assert row["is_new_alert"] is False
    lookup = [sql for sql, _ in cursor.executed if "SELECT id FROM mail_monitoring_notifications" in sql]
    assert lookup, "the existence check must run before the upsert"


def test_the_existence_check_is_keyed_the_way_the_table_is(monkeypatch):
    """`(gmail_message_id, classification)` is the table's own unique key. A
    check on anything else would answer a different question than the upsert."""
    cursor = FakeCursor(alert_exists=False)
    _install_cursor(monkeypatch, cursor)
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *_: (None, None))

    store.create_monitoring_notification(
        _alert_event(), {"id": "analysis-1", "classification": "offer_received", "candidate_status": "Offer Received"},
    )
    sql, params = next(
        (sql, params) for sql, params in cursor.executed
        if "SELECT id FROM mail_monitoring_notifications" in sql
    )
    assert "gmail_message_id=%s" in sql and "classification=%s" in sql
    assert params == ["gmail-1", "offer_received"]


def mailbox(mailbox_id, candidate_id="candidate-1"):
    return {"id": mailbox_id, "candidate_id": candidate_id, "email_address": f"{mailbox_id}@mail.test"}


def test_every_mailbox_is_queued_once_for_the_range(monkeypatch):
    queued = []
    audits = []
    monkeypatch.setattr(bulk_mail_rescan, "rescannable_mailboxes",
                        lambda: [mailbox("mailbox-1"), mailbox("mailbox-2", "candidate-2")])
    monkeypatch.setattr(store, "enqueue_historical_rescan",
                        lambda mid, **kw: queued.append((mid, kw)) or
                        {"id": f"job-{mid}", "status": "QUEUED", "reused_existing_job": False})
    monkeypatch.setattr(store, "audit", lambda **kw: audits.append(kw))

    result = bulk_mail_rescan.enqueue_range_rescan(
        range_start=date(2026, 8, 1), range_end=date(2026, 8, 28), requested_by="admin",
    )

    assert [row["mailbox_id"] for row in result["queued"]] == ["mailbox-1", "mailbox-2"]
    assert all(kw["range_start"] == date(2026, 8, 1) for _, kw in queued)
    assert [entry["action"] for entry in audits] == ["BULK_HISTORICAL_RESCAN_QUEUED"] * 2


def test_a_rescan_already_in_flight_is_reused_not_duplicated(monkeypatch):
    monkeypatch.setattr(bulk_mail_rescan, "rescannable_mailboxes", lambda: [mailbox("mailbox-1")])
    monkeypatch.setattr(store, "enqueue_historical_rescan",
                        lambda mid, **kw: {"id": "job-existing", "status": "RUNNING", "reused_existing_job": True})
    monkeypatch.setattr(store, "audit", lambda **kw: None)

    result = bulk_mail_rescan.enqueue_range_rescan(
        range_start=date(2026, 8, 1), range_end=date(2026, 8, 28),
    )
    assert result["queued"][0]["reused_existing_job"] is True


def test_one_mailbox_failing_to_queue_does_not_abandon_the_rest(monkeypatch):
    def enqueue(mailbox_id, **_):
        if mailbox_id == "mailbox-1":
            raise RuntimeError("mailbox row vanished")
        return {"id": f"job-{mailbox_id}", "status": "QUEUED", "reused_existing_job": False}

    monkeypatch.setattr(bulk_mail_rescan, "rescannable_mailboxes",
                        lambda: [mailbox("mailbox-1"), mailbox("mailbox-2")])
    monkeypatch.setattr(store, "enqueue_historical_rescan", enqueue)
    monkeypatch.setattr(store, "audit", lambda **kw: None)

    result = bulk_mail_rescan.enqueue_range_rescan(
        range_start=date(2026, 8, 1), range_end=date(2026, 8, 28),
    )
    assert [row["mailbox_id"] for row in result["queued"]] == ["mailbox-2"]
    assert result["failed"][0]["mailbox_id"] == "mailbox-1"


def test_a_reversed_range_is_refused():
    with pytest.raises(ValueError):
        bulk_mail_rescan.enqueue_range_rescan(range_start=date(2026, 8, 28), range_end=date(2026, 8, 1))


def test_completed_jobs_are_not_a_finished_rescan(monkeypatch):
    """Ingestion defers classification to the AI queue. Reporting when the jobs
    complete would count a month of unread mail as a month with no findings."""
    pending = iter([7, 3, 0])
    monkeypatch.setattr(bulk_mail_rescan, "job_progress",
                        lambda ids: {"jobs": 1, "by_status": {"COMPLETED": 1}, "outstanding": 0,
                                     "fetched": 10, "processed": 10, "events": 2, "failures": []})
    monkeypatch.setattr(store, "pending_ai_message_count", lambda **kw: next(pending))
    slept = []

    result = selection_rescan.wait_for_rescan(
        ["job-1"], range_start=date(2026, 8, 1), range_end=date(2026, 8, 28),
        sleep=slept.append, now=lambda: 0.0,
    )
    assert result["timed_out"] is False
    assert result["pending_ai_messages"] == 0
    assert len(slept) == 2, "it must keep waiting while the queue still holds mail"


def test_a_rescan_that_never_drains_times_out_and_says_so(monkeypatch):
    """Returning the partial state beats raising: what did finish is still worth
    reporting, and a run that quietly looked complete would be worse."""
    monkeypatch.setattr(bulk_mail_rescan, "job_progress",
                        lambda ids: {"jobs": 1, "by_status": {"RUNNING": 1}, "outstanding": 1,
                                     "fetched": 0, "processed": 0, "events": 0, "failures": []})
    monkeypatch.setattr(store, "pending_ai_message_count", lambda **kw: 4)
    clock = iter([0.0, 10.0, 999.0])

    result = selection_rescan.wait_for_rescan(
        ["job-1"], range_start=date(2026, 8, 1), range_end=date(2026, 8, 28),
        timeout_seconds=5, sleep=lambda _: None, now=lambda: next(clock),
    )
    assert result["timed_out"] is True
    assert result["pending_ai_messages"] == 4


def alert_row(notification_id, classification="offer_received", gmail_id=None, **overrides):
    return {
        "id": notification_id,
        "candidate_id": f"candidate-{notification_id}",
        "candidate_name": f"Candidate {notification_id}",
        "candidate_email": "candidate@mail.test",
        "classification": classification,
        "candidate_status": "Offer Received",
        "email_subject": f"Subject {notification_id}",
        "sender_email": "hr@company.test",
        "email_received_at": "2026-08-04T09:00:00Z",
        "ai_confidence": 0.95,
        "gmail_message_id": gmail_id or f"gmail-{notification_id}",
        **overrides,
    }


@pytest.fixture
def report_world(monkeypatch):
    """A whole run's worth of database answers, with sounds that succeed."""
    state = {
        "messages": [],
        "selection": [],
        "interview": [],
        "unclassified": [],
        "sound_events": {},
        "audits": [],
        "screen": [],
    }

    def alerts_in_range(*, range_start, range_end, classifications=None, created_after=None):
        if classifications == store.INTERVIEW_RELATED_CLASSIFICATIONS:
            return state["interview"]
        return state["selection"]

    monkeypatch.setattr(store, "messages_in_range", lambda **kw: state["messages"])
    monkeypatch.setattr(store, "alerts_in_range", alerts_in_range)
    monkeypatch.setattr(store, "unclassified_messages_in_range", lambda **kw: state["unclassified"])
    monkeypatch.setattr(store, "audit", lambda **kw: state["audits"].append(kw))
    monkeypatch.setattr(store, "mark_alert_sound_delivery", lambda nid, **kw: None)
    monkeypatch.setattr(store, "alert_sound_event",
                        lambda nid: state["sound_events"].get(nid))
    monkeypatch.setattr(
        "core.recruitment_realtime.publish",
        lambda event_type, **payload: state["sound_events"].setdefault(
            payload.get("notification_id"),
            {"id": f"event-{payload.get('notification_id')}", "notification_id": payload.get("notification_id")},
        ) and {"event_id": f"event-{payload.get('notification_id')}"},
    )
    monkeypatch.setattr(store, "list_notifications",
                        lambda **kw: (state["screen"], len(state["screen"])))
    return state


def _build(state, baseline=frozenset()):
    from datetime import datetime, timezone

    return selection_rescan.build_report(
        range_start=date(2026, 8, 1),
        range_end=date(2026, 8, 28),
        baseline=set(baseline),
        queue_result={"queued": [{"job_id": "job-1", "candidate_id": "candidate-1",
                                  "mailbox_id": "mailbox-1", "reused_existing_job": False}],
                      "failed": []},
        wait_result={"jobs": 1, "outstanding": 0, "timed_out": False, "by_status": {"COMPLETED": 1}},
        started_at=datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc),
    )


def test_alerts_that_already_existed_are_skipped_not_recovered(report_world):
    old = alert_row("old")
    fresh = alert_row("fresh")
    report_world["selection"] = [old, fresh]
    report_world["screen"] = [fresh]

    report = _build(report_world, baseline={("gmail-old", "offer_received")})

    assert report["totals"]["new_alerts_created"] == 1
    assert report["totals"]["duplicates_skipped"] == 1
    assert [row["notification_id"] for row in report["created_alerts"]] == ["fresh"]
    assert {entry["action"] for entry in report_world["audits"]} >= {"MAIL_ALERT_RESCAN_DUPLICATE_SKIPPED"}


def test_every_created_alert_is_reported_with_candidate_subject_and_status(report_world):
    report_world["selection"] = [alert_row("fresh")]
    report_world["screen"] = [alert_row("fresh")]

    entry = _build(report_world)["created_alerts"][0]
    assert entry["candidate_name"] == "Candidate fresh"
    assert entry["email_subject"] == "Subject fresh"
    assert entry["detected_status"] == "Offer Received"
    assert entry["sound"] == "DELIVERED"


def test_a_silent_alert_makes_the_run_unsuccessful(report_world, monkeypatch):
    """The alert is on the screen either way. Whether anyone was told is the
    difference between a recovered alert and one that is merely present."""
    report_world["selection"] = [alert_row("silent")]
    report_world["screen"] = [alert_row("silent")]
    monkeypatch.setattr("core.recruitment_realtime.publish",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no loop")))

    report = _build(report_world)
    assert report["totals"]["sound_notifications_triggered"] == 0
    assert report["totals"]["sound_notification_failures"] == 1
    assert report["successful"] is False
    assert report["sound_failures"][0]["notification_id"] == "silent"


def test_interview_alerts_in_range_are_counted_but_never_created_or_sounded(report_world):
    """Interview alerts are reported so the run can show it left them alone;
    they are not part of the selection recovery and get no new sound."""
    report_world["selection"] = [alert_row("fresh")]
    report_world["interview"] = [alert_row("iv", classification="interview_confirmed")]
    report_world["screen"] = [alert_row("fresh")]

    report = _build(report_world)
    assert report["totals"]["interview_alerts_in_range_untouched"] == 1
    assert [row["notification_id"] for row in report["created_alerts"]] == ["fresh"]
    assert "iv" not in {entry["notification_id"] for entry in report["sound_failures"]}


def test_mail_the_pipeline_rejected_is_counted_separately_from_mail_it_could_not_read(report_world):
    report_world["messages"] = [
        {"candidate_id": "candidate-1", "processing_status": "IGNORED_NOT_OFFER_RELATED"},
        {"candidate_id": "candidate-1", "processing_status": "DUPLICATE_CONTENT"},
        {"candidate_id": "candidate-2", "processing_status": "EVENT_CREATED"},
        {"candidate_id": "candidate-2", "processing_status": "AI_QUEUED"},
    ]
    report_world["unclassified"] = [
        {"candidate_id": "candidate-2", "email_address": "c2@mail.test", "subject": "Unread",
         "sender_email": "hr@company.test", "sent_at": "2026-08-20", "processing_status": "AI_QUEUED",
         "ignore_reason": None, "primary_status": None},
    ]
    report_world["screen"] = []

    report = _build(report_world)
    assert report["totals"]["non_selection_emails_rejected"] == 2
    assert report["totals"]["unclassified_emails"] == 1
    assert report["totals"]["emails_scanned"] == 4
    assert report["totals"]["candidates_scanned"] == 2
    assert report["unclassified_emails"][0]["email_subject"] == "Unread"


def test_the_screen_is_asked_with_the_filter_the_screen_itself_sends(report_world, monkeypatch):
    sent = {}

    def list_notifications(*, filters=None, limit=50, offset=0):
        sent.update(filters or {})
        return ([alert_row("fresh")], 1)

    monkeypatch.setattr(store, "list_notifications", list_notifications)
    report_world["selection"] = [alert_row("fresh")]

    report = _build(report_world)
    assert sent == {"classification_group": "selection"}
    assert report["selection_screen"]["confirmed"] is True


def test_an_alert_missing_from_the_screen_is_not_confirmed(report_world):
    """A row written to the table but absent from the screen's own query is an
    alert an operator cannot reach, however successfully it was created."""
    report_world["selection"] = [alert_row("fresh"), alert_row("hidden")]
    report_world["screen"] = [alert_row("fresh")]

    report = _build(report_world)
    assert report["selection_screen"]["confirmed"] is False
    assert report["selection_screen"]["missing"] == ["hidden"]
    assert report["successful"] is False
