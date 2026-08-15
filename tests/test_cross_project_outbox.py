from __future__ import annotations

from services import cross_project_outbox as outbox
from core.migrations import runner


def test_outbox_is_durable_idempotent_and_marks_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "_PATH", str(tmp_path / "outbox.json"))
    first = outbox.enqueue(event_type="marketing.notification.v1", idempotency_key="event-1", payload={"x": 1})
    second = outbox.enqueue(event_type="marketing.notification.v1", idempotency_key="event-1", payload={"x": 2})
    assert first["payload"] == second["payload"] == {"x": 1}
    assert [row["idempotency_key"] for row in outbox.due(event_type="marketing.notification.v1")] == ["event-1"]
    outbox.mark_delivered("event-1")
    assert outbox.due(event_type="marketing.notification.v1") == []


def test_outbox_failure_schedules_a_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "_PATH", str(tmp_path / "outbox.json"))
    outbox.enqueue(event_type="operations.opportunity.v1", idempotency_key="event-2", payload={})
    outbox.mark_failed("event-2", "temporary outage")
    assert outbox.due(event_type="operations.opportunity.v1") == []


def test_operations_migration_versions_are_unique():
    files = sorted(runner.MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    assert len(versions) == len(set(versions))
    assert "011_recruitment_mail_gmail_ingestion.sql" in {path.name for path in files}
