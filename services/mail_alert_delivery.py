"""Make the alert sound a fact on the record rather than a hope.

A Mail Alert is heard because the browser receives a `notification_created`
real-time event and plays a sound for it. Publishing that event is best effort
throughout the pipeline: every caller wraps it in `try/except` and logs at debug
level, because losing a chime must never lose a durable classification.

That trade is right for the live path and wrong for a backfill. A backfill
creates alerts for mail an operator has never been shown, and if the event is
lost the alert lands silently in a screen nobody has been given a reason to
open. Worse, the historical script that predated this module wrote alert rows
with raw SQL and never published anything at all, so *every* alert it created
was silent and nothing recorded that fact.

So this module owns the other half of the trade: for alerts that must be heard,
publish the event, then go back to the database and check it is there. An alert
whose event cannot be found is marked `INCOMPLETE` rather than counted as
delivered, and a run that produced any of those is not a successful run.

What can and cannot be proven here is worth stating plainly: this verifies that
the event which triggers the sound was durably recorded and broadcast. It cannot
observe a speaker. A dashboard that is closed when the event is published
replays it from `last_event_id` on its next connection, so the alert is still
heard; a dashboard that never opens hears nothing, and no server-side check can
say otherwise.
"""

from __future__ import annotations

import logging
from typing import Any

from core import recruitment_mail_store as store

logger = logging.getLogger("teleautomation.mail_alert_delivery")

DELIVERED = "DELIVERED"
INCOMPLETE = "INCOMPLETE"


def alert_event_payload(alert: dict[str, Any]) -> dict[str, Any]:
    """The `notification_created` payload the dashboard is built to read.

    Deliberately the same shape the live pipeline publishes: the browser decides
    whether to make a noise from `classification`, and the desktop notification
    is worded from `status`, `candidate_name` and `company_name`. A payload that
    drifts from this produces a silent event that still looks delivered.
    """
    return {
        "notification_id": alert.get("id"),
        "candidate_id": alert.get("candidate_id"),
        "candidate_name": alert.get("candidate_name"),
        "company_name": alert.get("company_name"),
        "classification": alert.get("classification"),
        "status": alert.get("candidate_status"),
        "confidence": round(float(alert.get("ai_confidence") or 0) * 100),
        "priority": alert.get("priority"),
    }


def _publish(payload: dict[str, Any]) -> str | None:
    from core.recruitment_realtime import publish

    envelope = publish("notification_created", **payload)
    return str(envelope.get("event_id") or "") or None


def deliver_alert_sound(alert: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
    """Ensure one alert has a published sound event, and record the outcome.

    Idempotent by design: an alert the live pipeline already announced keeps its
    original event rather than being announced twice. Re-running a rescan must
    not turn one piece of news into two chimes.
    """
    notification_id = str(alert.get("id") or "")
    if not notification_id:
        raise ValueError("Alert has no id; nothing can be delivered for it")

    existing = store.alert_sound_event(notification_id)
    published_now = False
    if existing is None:
        try:
            _publish(alert_event_payload(alert))
            published_now = True
        except Exception:
            # Not fatal here: the verification below is what decides the
            # outcome, and it reads the database rather than trusting this call.
            logger.exception("Alert sound event could not be published notification_id=%s", notification_id)

    # Verify against storage rather than against the call above having returned.
    # A publish that raised after writing, or wrote nothing after returning, is
    # exactly the case this exists to catch.
    event = store.alert_sound_event(notification_id)
    status = DELIVERED if event else INCOMPLETE
    event_id = str(event.get("id")) if event else None
    store.mark_alert_sound_delivery(notification_id, status=status, event_id=event_id)
    store.audit(
        actor=actor,
        role="system",
        action="MAIL_ALERT_SOUND_DELIVERED" if event else "MAIL_ALERT_SOUND_INCOMPLETE",
        candidate_id=alert.get("candidate_id"),
        source_id=notification_id,
        new={
            "classification": alert.get("classification"),
            "candidate_status": alert.get("candidate_status"),
            "email_subject": alert.get("email_subject"),
            "sound_delivery_status": status,
            "sound_delivery_event_id": event_id,
            "published_by_this_run": published_now,
        },
    )
    return {
        "notification_id": notification_id,
        "candidate_id": alert.get("candidate_id"),
        "candidate_name": alert.get("candidate_name"),
        "classification": alert.get("classification"),
        "candidate_status": alert.get("candidate_status"),
        "email_subject": alert.get("email_subject"),
        "status": status,
        "event_id": event_id,
        "published_by_this_run": published_now,
    }


def deliver_alert_sounds(alerts: list[dict[str, Any]], *, actor: str = "system") -> dict[str, Any]:
    """Deliver and verify a batch, keeping both halves of the outcome.

    Failures are returned alongside successes rather than raised: a rescan that
    stopped at the first undeliverable alert would leave the rest of the month
    unannounced, which is the failure it was run to fix.
    """
    delivered: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for alert in alerts:
        try:
            outcome = deliver_alert_sound(alert, actor=actor)
        except Exception as exc:
            logger.exception("Alert sound delivery failed notification_id=%s", alert.get("id"))
            outcome = {
                "notification_id": alert.get("id"),
                "candidate_id": alert.get("candidate_id"),
                "candidate_name": alert.get("candidate_name"),
                "classification": alert.get("classification"),
                "candidate_status": alert.get("candidate_status"),
                "email_subject": alert.get("email_subject"),
                "status": INCOMPLETE,
                "event_id": None,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }
        (delivered if outcome["status"] == DELIVERED else incomplete).append(outcome)
    return {"delivered": delivered, "incomplete": incomplete}
