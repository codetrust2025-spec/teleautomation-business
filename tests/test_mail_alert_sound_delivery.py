"""An alert nobody hears is the failure these tests exist to catch.

The Mail Alerts screen gets its sound from one place: the browser plays a
notification when a `notification_created` real-time event reaches it. Every
publisher of that event wraps the call in `try/except` and logs at debug, so a
lost event leaves an alert sitting on a screen with nothing to draw an operator
to it -- and nothing anywhere recording that it happened.

The historical backfill made this concrete. It wrote alert rows with raw SQL and
published nothing at all, so every alert it created was silent, and a report
counting "alerts created" called that a success.

So these tests assert on the event, not on the alert row: that a genuinely new
alert produces one, that re-reading the same mail does not produce a second, and
that an alert which ends up without one is marked INCOMPLETE rather than counted
as delivered.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import mail_alert_delivery


def alert(**overrides):
    return {
        "id": "notification-1",
        "candidate_id": "candidate-1",
        "candidate_name": "Asha Rao",
        "company_name": "Test Company",
        "classification": "offer_received",
        "candidate_status": "Offer Received",
        "email_subject": "We are pleased to offer you the role",
        "ai_confidence": 0.94,
        "priority": "critical",
        **overrides,
    }


class Recorder:
    """Stands in for the durable event table and the alert row."""

    def __init__(self, *, existing_event=None, publish_writes=True, publish_raises=False,
                 undeliverable=()):
        self.events = {existing_event["notification_id"]: existing_event} if existing_event else {}
        self.publish_writes = publish_writes
        self.publish_raises = publish_raises
        self.undeliverable = set(undeliverable)
        self.published: list[tuple[str, dict]] = []
        self.marked: list[dict] = []
        self.audited: list[dict] = []

    def publish(self, event_type, **payload):
        self.published.append((event_type, payload))
        notification_id = payload.get("notification_id")
        if self.publish_raises:
            raise RuntimeError("websocket loop is gone")
        if self.publish_writes and notification_id not in self.undeliverable:
            self.events[notification_id] = {"id": f"event-{notification_id}", "event_type": event_type,
                                            "notification_id": notification_id}
        return {"event_id": f"event-{notification_id}", **payload}

    def alert_sound_event(self, notification_id):
        return self.events.get(notification_id)

    def mark(self, notification_id, *, status, event_id=None):
        self.marked.append({"notification_id": notification_id, "status": status, "event_id": event_id})
        return {"id": notification_id, "sound_delivery_status": status}

    def audit(self, **kwargs):
        self.audited.append(kwargs)


@pytest.fixture
def recorder(monkeypatch):
    def _install(**kwargs):
        rec = Recorder(**kwargs)
        monkeypatch.setattr(store, "alert_sound_event", rec.alert_sound_event)
        monkeypatch.setattr(store, "mark_alert_sound_delivery", rec.mark)
        monkeypatch.setattr(store, "audit", rec.audit)
        monkeypatch.setattr("core.recruitment_realtime.publish", rec.publish)
        return rec
    return _install


def test_a_new_alert_gets_a_sound_event_and_is_marked_delivered(recorder):
    rec = recorder()
    outcome = mail_alert_delivery.deliver_alert_sound(alert())

    assert [event_type for event_type, _ in rec.published] == ["notification_created"]
    assert outcome["status"] == mail_alert_delivery.DELIVERED
    assert rec.marked == [
        {"notification_id": "notification-1", "status": "DELIVERED", "event_id": "event-notification-1"},
    ]
    assert rec.audited[0]["action"] == "MAIL_ALERT_SOUND_DELIVERED"


def test_the_payload_carries_what_the_browser_reads(recorder):
    """The browser decides whether to make a noise from `classification`, and
    words the desktop notification from `status` and the names. A payload that
    drops those is an event that arrives and does nothing."""
    rec = recorder()
    mail_alert_delivery.deliver_alert_sound(alert())

    _, payload = rec.published[0]
    assert payload["classification"] == "offer_received"
    assert payload["status"] == "Offer Received"
    assert payload["notification_id"] == "notification-1"
    assert payload["candidate_name"] == "Asha Rao"
    assert payload["confidence"] == 94


def test_an_alert_that_already_sounded_is_not_announced_twice(recorder):
    """A rescan re-reads mail the live pipeline already announced. Publishing
    again would turn one piece of news into two chimes."""
    rec = recorder(existing_event={"id": "event-original", "notification_id": "notification-1"})
    outcome = mail_alert_delivery.deliver_alert_sound(alert())

    assert rec.published == []
    assert outcome["status"] == mail_alert_delivery.DELIVERED
    assert outcome["event_id"] == "event-original"
    assert outcome["published_by_this_run"] is False


def test_an_alert_whose_event_never_lands_is_marked_incomplete(recorder):
    """Publishing raising is not the interesting case on its own -- the
    interesting case is that afterwards the event is still not there. The
    outcome is read back from storage rather than from the call returning."""
    rec = recorder(publish_raises=True)
    outcome = mail_alert_delivery.deliver_alert_sound(alert())

    assert outcome["status"] == mail_alert_delivery.INCOMPLETE
    assert outcome["event_id"] is None
    assert rec.marked == [{"notification_id": "notification-1", "status": "INCOMPLETE", "event_id": None}]
    assert rec.audited[0]["action"] == "MAIL_ALERT_SOUND_INCOMPLETE"


def test_a_publish_that_returns_without_writing_is_not_delivered(recorder):
    """A publisher can return normally and still leave nothing behind. Trusting
    the return value is how a silent alert gets counted as an announced one."""
    rec = recorder(publish_writes=False)
    outcome = mail_alert_delivery.deliver_alert_sound(alert())

    assert rec.published, "the attempt should still have been made"
    assert outcome["status"] == mail_alert_delivery.INCOMPLETE


def test_a_batch_keeps_going_after_one_alert_cannot_be_delivered(recorder):
    """Stopping at the first failure would leave the rest of the month
    unannounced, which is the failure the rescan was run to fix."""
    rec = recorder(undeliverable={"notification-2"})

    result = mail_alert_delivery.deliver_alert_sounds([
        alert(id="notification-1"),
        alert(id="notification-2", candidate_name="Silent Case"),
        alert(id="notification-3"),
    ])

    assert [entry["notification_id"] for entry in result["delivered"]] == ["notification-1", "notification-3"]
    assert [entry["notification_id"] for entry in result["incomplete"]] == ["notification-2"]


def test_an_alert_without_an_id_is_refused_rather_than_reported_delivered(recorder):
    recorder()
    with pytest.raises(ValueError):
        mail_alert_delivery.deliver_alert_sound(alert(id=None))


def _pipeline(monkeypatch, notification):
    """Drive `process_message` far enough to reach the publish decision.

    Deliberately the real function rather than a stand-in for it: the gate under
    test is one line inside it, and a test that reimplemented the surrounding
    flow would keep passing after that line was removed.
    """
    from services import recruitment_mail_agent as agent

    published: list[tuple[str, dict]] = []
    event = {
        "id": "event-1",
        "candidate_id": "candidate-1",
        "classification": "offer_received",
        "candidate_status": "Offer Received",
        "confidence": 0.95,
        "primary_status": "OFFER_LETTER_RECEIVED",
        "notification": notification,
    }
    result = {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": "OFFER_LETTER_RECEIVED",
        "primary_status": "OFFER_LETTER_RECEIVED",
        "classification": "offer_received",
        "candidate_status": "Offer Received",
        "confidence": 0.95,
        "company": {"name": "Test Company"},
        "job": {"title": "Engineer"},
        "interview": {},
        "offer": {},
        "evidence": [{"source": "EMAIL_BODY", "meaning": "OFFER_LETTER_RECEIVED", "text": "offer letter"}],
        "requires_manual_review": False,
        "summary": "Offer letter received.",
    }
    monkeypatch.setattr(agent.store, "insert_message", lambda *a, **k: ({"id": "message-1"}, True))
    monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *a: False)
    monkeypatch.setattr(agent.store, "is_duplicate_offer_attachment", lambda *a: False)
    monkeypatch.setattr(agent.store, "is_duplicate_thread_status", lambda *a: False)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda *a, **k: None)
    monkeypatch.setattr(agent.store, "create_event", lambda *a, **k: event)
    monkeypatch.setattr(agent, "analyze", lambda *a: (result, "test-model", 5))
    monkeypatch.setattr("services.recruitment_notifications.notify_detection", lambda event: None)
    monkeypatch.setattr(
        "core.recruitment_realtime.publish",
        lambda event_type, **payload: published.append((event_type, payload)) or {"event_id": "e"},
    )

    agent.process_message(
        {"id": "mailbox-1", "candidate_id": "candidate-1"},
        {"provider_message_id": "gmail-1", "provider_thread_id": "thread-1",
         "sender_email": "hr" + "@" + "company.test", "recipient_email": "asha" + "@" + "mail.test",
         "subject": "We are pleased to offer you the role", "sent_at": "2026-08-04T09:00:00Z",
         "body": "We are pleased to offer you the Engineer role. Offer letter attached."},
        [],
    )
    return [event_type for event_type, _ in published]


def test_a_newly_created_alert_is_announced(monkeypatch):
    events = _pipeline(monkeypatch, {"id": "notification-1", "is_new_alert": True})
    assert "notification_created" in events


def test_re_reading_mail_whose_alert_exists_makes_no_new_announcement(monkeypatch):
    """A historical rescan walks a month of mail whose alerts are already on the
    screen. Announcing them again would fire hundreds of chimes for news nobody
    is hearing for the first time."""
    events = _pipeline(monkeypatch, {"id": "notification-1", "is_new_alert": False})
    assert "notification_created" not in events
    assert "mail_classified" in events, "the rest of the pipeline must be unaffected"


def test_an_alert_that_cannot_say_is_announced(monkeypatch):
    """Callers that predate the flag must keep working. Defaulting to silence
    would turn a missing key into a missing alert sound."""
    events = _pipeline(monkeypatch, {"id": "notification-1"})
    assert "notification_created" in events
