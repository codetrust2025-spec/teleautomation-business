"""Registering and renewing Gmail push watches.

A Gmail watch is not permanent: Google expires it seven days after it is
registered, and an expired watch stops delivering push notifications silently -
no error, no callback, mail simply stops arriving.  Renewal therefore has to
happen on a schedule rather than only when a human touches a mailbox.

This module exists so the renewal can be shared.  It previously lived inside
`core.recruitment_mail_api`, which imports FastAPI, the candidate store and the
dashboard access layer; a background loop should not have to pull all of that in
to re-register a watch.  Only the store and the Gmail provider are needed here.

Renewal is deliberately best-effort.  Push is an optimisation on top of the
scheduler's polling, so a mailbox whose watch cannot be renewed keeps syncing on
the polling path.  Nothing in here may mark a mailbox unhealthy or disable
monitoring.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from core import recruitment_mail_store as store
from services.gmail_mailbox_provider import GmailMailboxProvider

logger = logging.getLogger(__name__)

# Google expires a watch after seven days.  Renewing a full day early leaves
# room for the hourly tick to retry around a transient Google outage instead of
# racing the deadline.
WATCH_LIFETIME = timedelta(days=7)
RENEWAL_LEAD = timedelta(hours=24)


def configured_topic() -> str:
    """The Pub/Sub topic watches are registered against, or '' if unset."""
    return (os.getenv("GMAIL_PUBSUB_TOPIC") or "").strip()


def _expiration_from(result: dict[str, Any]) -> datetime | None:
    """Gmail reports watch expiry as epoch milliseconds, as a string."""
    try:
        return datetime.fromtimestamp(int(result.get("expiration")) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def renew_watch(mailbox: dict[str, Any]) -> dict[str, Any] | None:
    """Re-register the Gmail watch for one mailbox and record its new expiry.

    Returns the updated mailbox row, or None when there is nothing to do (no
    topic configured, or no stored credential).  Raises whatever the Gmail
    provider raises - callers decide whether a failure is fatal.
    """
    topic = configured_topic()
    if not topic or not mailbox.get("credential_ciphertext"):
        return None
    result = GmailMailboxProvider(mailbox["credential_ciphertext"]).start_watch(topic)
    return store.update_mailbox(
        mailbox["id"],
        {
            # Keep whatever cursor the mailbox already has.  Renewal must not
            # rewind or fast-forward an actively syncing mailbox; the historyId
            # Gmail returns here is only a seed for a mailbox that has none.
            "provider_history_id": str(
                mailbox.get("provider_history_id") or result.get("historyId") or ""
            ),
            "sync_cursor": str(mailbox.get("sync_cursor") or result.get("historyId") or ""),
            "gmail_watch_expiration": _expiration_from(result),
            "gmail_watch_topic": topic,
        },
    )


def renew_due_watches(*, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    """Renew every watch that lapses within the lead window.

    Returns counts of {'due', 'renewed', 'failed'}.  Individual failures are
    logged and skipped so that one revoked mailbox cannot stop the others from
    being renewed.
    """
    if not configured_topic():
        return {"due": 0, "renewed": 0, "failed": 0}
    moment = now or datetime.now(timezone.utc)
    due = store.mailboxes_due_for_watch_renewal(before=moment + RENEWAL_LEAD, limit=limit)
    renewed = failed = 0
    for mailbox in due:
        try:
            renew_watch(mailbox)
            renewed += 1
        except Exception:
            failed += 1
            # Identify the mailbox by id, not address: this line goes to the
            # container log.
            logger.warning(
                "Gmail watch renewal failed mailbox_id=%s; polling fallback remains active",
                mailbox.get("id"),
                exc_info=True,
            )
    return {"due": len(due), "renewed": renewed, "failed": failed}
