"""Rescan a month of mail for every candidate, and prove what came of it.

This is the orchestration a person means by "rescan August and make sure every
selection mail is on the Mail Alerts screen with a sound". It owns no
classification and no alert SQL of its own -- it queues the pipeline's own
historical rescan jobs, waits for the pipeline to finish thinking, and then
reports what the pipeline decided.

The reporting half exists because the counts are the part that is easy to get
wrong in a reassuring direction. Three of them in particular:

* An alert that already existed is not a new alert. A rescan re-reads mail an
  operator has already been shown, and counting those as recoveries would make
  every run look like it rescued a month's worth of missed news.
* A message still sitting on the AI queue has not been cleared -- nobody has
  looked at it yet. Reporting when the Gmail jobs go green, rather than when the
  queue drains, would count unread mail as mail with no findings.
* An alert with no `notification_created` event behind it made no sound. It is
  on the screen, so a query for "alerts created" counts it, and an operator who
  was never told still does not know it is there.

Each of those is counted the way it actually is, and a run with any silent alert
is not reported as a success.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Callable

from core import recruitment_mail_store as store
from services import bulk_mail_rescan
from services.mail_alert_delivery import DELIVERED, deliver_alert_sounds

logger = logging.getLogger("teleautomation.selection_rescan")

SELECTION = store.SELECTION_RELATED_CLASSIFICATIONS
INTERVIEW = store.INTERVIEW_RELATED_CLASSIFICATIONS

# Terminal message states that mean the pipeline looked at a mail and decided it
# was not a lifecycle event. Kept explicit so a new state added later shows up as
# unclassified -- which is true -- rather than being silently counted as cleared.
REJECTED_MESSAGE_STATUSES = {
    "IGNORED_NOT_OFFER_RELATED",
    "IGNORED_LOW_CONFIDENCE",
    "DUPLICATE_CONTENT",
    "DUPLICATE_OFFER_EVENT",
    "DUPLICATE_OFFER_ATTACHMENT",
    "NO_RELEVANT_STATUS",
}


def _alert_key(alert: dict[str, Any]) -> tuple[str, str]:
    """What makes two alert rows the same alert, as the database defines it."""
    return (str(alert.get("gmail_message_id") or ""), str(alert.get("classification") or ""))


def selection_alert_baseline(*, range_start: date, range_end: date) -> set[tuple[str, str]]:
    """The selection alerts that were already on the screen before a run.

    Taken before anything is queued. Without it there is no way afterwards to
    tell an alert this run recovered from one that has been sitting there for
    weeks, and the difference is the entire point of the exercise.
    """
    return {
        _alert_key(alert)
        for alert in store.alerts_in_range(
            range_start=range_start, range_end=range_end, classifications=SELECTION,
        )
    }


def wait_for_rescan(
    job_ids: list[str],
    *,
    range_start: date,
    range_end: date,
    timeout_seconds: int = 7200,
    poll_seconds: int = 15,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Wait until the jobs are done *and* the AI queue for the range is empty.

    Gmail ingestion defers every message to the durable AI queue, so a completed
    job means the mail is stored, not that it has been classified. Alerts appear
    only as the recovery worker drains that queue.

    Returns rather than raises on timeout: a partial rescan that says so is more
    useful than an exception that discards what did finish.
    """
    deadline = now() + timeout_seconds
    while True:
        progress = bulk_mail_rescan.job_progress(job_ids)
        pending_ai = store.pending_ai_message_count(range_start=range_start, range_end=range_end)
        finished = progress["outstanding"] == 0 and pending_ai == 0
        if finished:
            return {**progress, "pending_ai_messages": 0, "timed_out": False}
        if now() >= deadline:
            logger.warning(
                "Rescan wait timed out outstanding_jobs=%s pending_ai=%s",
                progress["outstanding"], pending_ai,
            )
            return {**progress, "pending_ai_messages": pending_ai, "timed_out": True}
        sleep(poll_seconds)


def _describe_alert(alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "notification_id": alert.get("id"),
        "candidate_id": alert.get("candidate_id"),
        "candidate_name": alert.get("candidate_name"),
        "candidate_email": alert.get("candidate_email"),
        "email_subject": alert.get("email_subject"),
        "sender_email": alert.get("sender_email"),
        "email_received_at": str(alert.get("email_received_at") or ""),
        "classification": alert.get("classification"),
        "detected_status": alert.get("candidate_status"),
        "confidence": alert.get("ai_confidence"),
        "gmail_message_id": alert.get("gmail_message_id"),
    }


def verify_selection_screen(expected_ids: set[str]) -> dict[str, Any]:
    """Ask the screen's own query whether the recovered alerts are on it.

    Deliberately `list_notifications` with the group filter the Mail Alerts
    screen sends, not a hand-written query: a report that verified against
    different SQL from the screen would confirm rows the operator cannot see.
    """
    if not expected_ids:
        return {"expected": 0, "visible": 0, "missing": [], "confirmed": True}
    found: set[str] = set()
    offset = 0
    page = 200
    while True:
        rows, total = store.list_notifications(
            filters={"classification_group": "selection"}, limit=page, offset=offset,
        )
        if not rows:
            break
        found.update(str(row.get("id")) for row in rows)
        offset += len(rows)
        if offset >= int(total or 0):
            break
    missing = sorted(expected_ids - found)
    return {
        "expected": len(expected_ids),
        "visible": len(expected_ids) - len(missing),
        "missing": missing,
        "confirmed": not missing,
    }


def build_report(
    *,
    range_start: date,
    range_end: date,
    baseline: set[tuple[str, str]],
    queue_result: dict[str, Any],
    wait_result: dict[str, Any],
    started_at: datetime,
    actor: str = "system",
) -> dict[str, Any]:
    """Assemble the run's findings, and deliver a sound for each new alert."""
    messages = store.messages_in_range(range_start=range_start, range_end=range_end)
    selection_alerts = store.alerts_in_range(
        range_start=range_start, range_end=range_end, classifications=SELECTION,
    )
    interview_alerts = store.alerts_in_range(
        range_start=range_start, range_end=range_end, classifications=INTERVIEW,
    )
    new_alerts = [a for a in selection_alerts if _alert_key(a) not in baseline]
    duplicates = [a for a in selection_alerts if _alert_key(a) in baseline]

    for alert in duplicates:
        store.audit(
            actor=actor,
            role="system",
            action="MAIL_ALERT_RESCAN_DUPLICATE_SKIPPED",
            candidate_id=alert.get("candidate_id"),
            source_id=str(alert.get("id") or ""),
            new={
                "classification": alert.get("classification"),
                "email_subject": alert.get("email_subject"),
                "gmail_message_id": alert.get("gmail_message_id"),
                "reason": "ALERT_ALREADY_PRESENT",
            },
        )

    sound = deliver_alert_sounds(new_alerts, actor=actor)
    unclassified = store.unclassified_messages_in_range(range_start=range_start, range_end=range_end)
    rejected = [
        m for m in messages
        if str(m.get("processing_status") or "").upper() in REJECTED_MESSAGE_STATUSES
    ]
    screen = verify_selection_screen({str(a.get("id")) for a in new_alerts})

    candidates = {str(m.get("candidate_id")) for m in messages if m.get("candidate_id")}
    mailboxes = queue_result.get("queued") or []
    scanned_candidates = candidates | {
        str(row.get("candidate_id")) for row in mailboxes if row.get("candidate_id")
    }

    successful = wait_result.get("timed_out") is False and not sound["incomplete"] and screen["confirmed"]
    return {
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "successful": bool(successful and not queue_result.get("failed")),
        "totals": {
            "candidates_scanned": len(scanned_candidates),
            "mailboxes_rescanned": len(mailboxes),
            "emails_scanned": len(messages),
            "selection_emails_detected": len(selection_alerts),
            "new_alerts_created": len(new_alerts),
            "duplicates_skipped": len(duplicates),
            "non_selection_emails_rejected": len(rejected),
            "sound_notifications_triggered": len(sound["delivered"]),
            "sound_notification_failures": len(sound["incomplete"]),
            "unclassified_emails": len(unclassified),
            "interview_alerts_in_range_untouched": len(interview_alerts),
        },
        "jobs": {
            "queued": len(mailboxes),
            "reused_existing": len([row for row in mailboxes if row.get("reused_existing_job")]),
            "queue_failures": queue_result.get("failed") or [],
            "progress": wait_result,
        },
        "created_alerts": [
            {
                **_describe_alert(alert),
                "sound": next(
                    (
                        entry for entry in sound["delivered"] + sound["incomplete"]
                        if entry.get("notification_id") == alert.get("id")
                    ),
                    {"status": "UNKNOWN"},
                )["status"],
            }
            for alert in new_alerts
        ],
        "duplicates_skipped": [_describe_alert(alert) for alert in duplicates],
        "sound_failures": sound["incomplete"],
        "unclassified_emails": [
            {
                "candidate_id": row.get("candidate_id"),
                "mailbox": row.get("email_address"),
                "email_subject": row.get("subject"),
                "sender_email": row.get("sender_email"),
                "sent_at": str(row.get("sent_at") or ""),
                "processing_status": row.get("processing_status"),
                "reason": row.get("ignore_reason") or row.get("primary_status"),
            }
            for row in unclassified
        ],
        "selection_screen": screen,
    }


def run(
    *,
    range_start: date,
    range_end: date,
    actor: str = "system",
    timeout_seconds: int = 7200,
    poll_seconds: int = 15,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Queue the rescan, wait for the pipeline, then report and deliver sounds."""
    started_at = datetime.now(timezone.utc)
    baseline = selection_alert_baseline(range_start=range_start, range_end=range_end)
    queue_result = bulk_mail_rescan.enqueue_range_rescan(
        range_start=range_start, range_end=range_end, requested_by=actor,
    )
    job_ids = [str(row["job_id"]) for row in queue_result["queued"] if row.get("job_id")]
    wait_result = wait_for_rescan(
        job_ids,
        range_start=range_start,
        range_end=range_end,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
    )
    report = build_report(
        range_start=range_start,
        range_end=range_end,
        baseline=baseline,
        queue_result=queue_result,
        wait_result=wait_result,
        started_at=started_at,
        actor=actor,
    )
    store.audit(
        actor=actor,
        role="system",
        action="SELECTION_RESCAN_COMPLETED" if report["successful"] else "SELECTION_RESCAN_INCOMPLETE",
        source_id=None,
        new={
            "range_start": report["range_start"],
            "range_end": report["range_end"],
            **report["totals"],
        },
    )
    return report
