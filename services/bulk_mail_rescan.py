"""Rescan every connected mailbox over a date range, through the live pipeline.

There was already a way to rescan one candidate's mail for a date range: the
mailbox worker's `HISTORICAL_RESCAN` job fetches that range from Gmail, decodes
each message with its thread context and attachments, and hands it to
`process_message(..., reprocess=True)` -- the same function the live path calls,
classifying through the same Ollama gateway, writing the same events, alerts and
real-time notifications.

What was missing was doing it for *everyone*. The API exposed the rescan per
candidate, so "rescan August for every candidate" meant an operator calling one
endpoint per mailbox and hoping none were missed.

The alternative that existed instead -- a standalone script that read stored
rows and wrote alert SQL directly -- is exactly the mistake this repository has
been bitten by before: it classified with its own deterministic rules rather
than the model production uses, and it created alerts through a path that
publishes nothing, so it could not make a sound. A green run of it proved
nothing about the pipeline it was standing in for.

So this enqueues the real jobs and lets the real worker do the work. It adds no
classification of its own, which is the point: there is only one classifier, and
a rescan that disagreed with production would be a second one.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from core import recruitment_mail_store as store

logger = logging.getLogger("teleautomation.bulk_mail_rescan")

TERMINAL_JOB_STATUSES = {"COMPLETED", "FAILED", "DEAD_LETTER"}


def rescannable_mailboxes() -> list[dict[str, Any]]:
    """Mailboxes a rescan can actually read.

    A rescan re-fetches from Gmail, so it needs credentials; `mailbox_health_rows`
    already returns exactly the non-superseded mailboxes that hold them, without
    exposing the ciphertext itself.
    """
    return store.mailbox_health_rows()


def enqueue_range_rescan(
    *,
    range_start: date,
    range_end: date,
    requested_by: str = "system",
    mailboxes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Queue one historical rescan per mailbox and record who asked for it.

    Idempotent through `enqueue_historical_rescan`, which returns a mailbox's
    already-queued or running rescan instead of creating a second one. Re-running
    after an interruption therefore resumes rather than doubling the work.
    """
    if range_start > range_end:
        raise ValueError("range_start must not be after range_end")

    targets = rescannable_mailboxes() if mailboxes is None else mailboxes
    queued: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for mailbox in targets:
        mailbox_id = str(mailbox.get("id") or "")
        candidate_id = str(mailbox.get("candidate_id") or "") or None
        try:
            job = store.enqueue_historical_rescan(
                mailbox_id,
                requested_by=requested_by,
                range_start=range_start,
                range_end=range_end,
            )
        except Exception as exc:
            logger.exception("Rescan could not be queued mailbox_id=%s", mailbox_id)
            failed.append({
                "mailbox_id": mailbox_id,
                "candidate_id": candidate_id,
                "email_address": mailbox.get("email_address"),
                "error": f"{type(exc).__name__}: {exc}"[:300],
            })
            continue
        # Whether this call queued the job or found one already queued is worth
        # keeping: a resumed run should not read as if it queued the month twice.
        reused = bool(job.get("reused_existing_job"))
        queued.append({
            "job_id": job.get("id"),
            "mailbox_id": mailbox_id,
            "candidate_id": candidate_id,
            "email_address": mailbox.get("email_address"),
            "status": job.get("status"),
            "reused_existing_job": reused,
        })
        store.audit(
            actor=requested_by,
            role="system",
            action="BULK_HISTORICAL_RESCAN_QUEUED",
            candidate_id=candidate_id,
            source_id=str(job.get("id") or ""),
            new={
                "mailbox_id": mailbox_id,
                "email_address": mailbox.get("email_address"),
                "range_start": range_start.isoformat(),
                "range_end": range_end.isoformat(),
                "reused_existing_job": reused,
            },
        )
    return {
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "mailboxes": len(targets),
        "queued": queued,
        "failed": failed,
    }


def job_progress(job_ids: list[str]) -> dict[str, Any]:
    """How far a queued batch has got, without interpreting it as finished."""
    rows = store.sync_jobs_by_ids(job_ids)
    by_status: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or "UNKNOWN")
        by_status[key] = by_status.get(key, 0) + 1
    outstanding = [
        row for row in rows
        if str(row.get("status") or "") not in TERMINAL_JOB_STATUSES
    ]
    return {
        "jobs": len(rows),
        "by_status": by_status,
        "outstanding": len(outstanding),
        "fetched": sum(int(row.get("messages_fetched") or 0) for row in rows),
        "processed": sum(int(row.get("messages_processed") or 0) for row in rows),
        "events": sum(int(row.get("events_detected") or 0) for row in rows),
        "failures": [
            {
                "job_id": row.get("id"),
                "mailbox_id": row.get("mailbox_id"),
                "status": row.get("status"),
                "error": row.get("error_message"),
            }
            for row in rows
            if str(row.get("status") or "") in {"FAILED", "DEAD_LETTER"}
        ],
    }
