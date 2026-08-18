"""PostgreSQL persistence for candidate mailbox recruitment tracking."""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Any

from core.db.connection import get_connection, use_postgres
from core.recruitment_offer_visibility import (
    qualified_event_sql,
    should_show_in_selection_offer_review,
)


CANONICAL_CLASSIFICATIONS = {
    "job_selection_confirmed", "offer_received", "offer_accepted",
    "offer_declined", "offer_revoked", "joining_confirmed",
    "joining_date_updated", "onboarding_started", "background_verification",
    "document_verification", "compensation_confirmation", "interview_update",
    "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
    "interview_cancelled", "candidate_rejected", "needs_review",
    "not_relevant", "final_round_cleared", "hr_confirmation",
}

# Mail Monitoring Notifications track only auto interview slot booking and
# job confirmed monitoring mails. Other classifications are still processed
# for candidate status and offer tracking but do not produce user-facing
# notifications.
TRACKED_NOTIFICATION_CLASSIFICATIONS = {
    "job_selection_confirmed", "offer_received", "offer_accepted",
    "offer_declined", "offer_revoked", "joining_confirmed",
    "joining_date_updated", "onboarding_started", "background_verification",
    "document_verification", "compensation_confirmation",
    "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
    "interview_cancelled", "candidate_rejected",
    "final_round_cleared", "hr_confirmation",
}
IMPORTANT_ALERT_EVIDENCE_MEANINGS = {
    "SELECTED", "FINAL_SELECTION_CONFIRMED", "JOB_SELECTION_CONFIRMED",
    "FINAL_ROUND_CLEARED", "INTERVIEW_CLEARED",
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED",
    "OFFER_RECEIVED", "OFFER_ACCEPTED", "OFFER_DECLINED", "OFFER_REVOKED",
    "JOINING_CONFIRMED", "JOINING_DATE_UPDATED", "POST_SELECTION_ONBOARDING",
    "ONBOARDING_STARTED", "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION",
    "HR_CONFIRMATION", "COMPENSATION_CONFIRMATION", "INTERVIEW_SHORTLISTED",
    "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
    "CANDIDATE_REJECTED",
}

_STATUS_CLASSIFICATION = {
    "SELECTED": "job_selection_confirmed",
    "FINAL_SELECTION_CONFIRMED": "job_selection_confirmed",
    "FINAL_ROUND_CLEARED": "final_round_cleared",
    "OFFER_INDICATION": "offer_received",
    "OFFER_IN_PROGRESS": "offer_received",
    "OFFER_APPROVED": "offer_received",
    "OFFER_LETTER_RECEIVED": "offer_received",
    "APPOINTMENT_LETTER_RECEIVED": "offer_received",
    "OFFER_RECEIVED": "offer_received",
    "OFFER_ACCEPTED": "offer_accepted",
    "OFFER_DECLINED": "offer_declined",
    "OFFER_REVOKED": "offer_revoked",
    "JOINING_CONFIRMED": "joining_confirmed",
    "JOINING_DATE_UPDATED": "joining_confirmed",
    "POST_SELECTION_ONBOARDING": "joining_confirmed",
    "JOINED": "joining_confirmed",
    "BACKGROUND_VERIFICATION": "joining_confirmed",
    "DOCUMENT_VERIFICATION": "hr_confirmation",
    "HR_CONFIRMATION": "hr_confirmation",
    "COMPENSATION_CONFIRMATION": "hr_confirmation",
    "INTERVIEW_UPDATE": "interview_update",
    "INTERVIEW_SHORTLISTED": "interview_shortlisted",
    "INTERVIEW_CONFIRMED": "interview_confirmed",
    "INTERVIEW_RESCHEDULED": "interview_rescheduled",
    "INTERVIEW_CANCELLED": "interview_cancelled",
    "CANDIDATE_REJECTED": "candidate_rejected",
    "MANUAL_REVIEW_REQUIRED": "needs_review",
    "IGNORED_LOW_CONFIDENCE": "needs_review",
    "IGNORED_NOT_OFFER_RELATED": "not_relevant",
}

_CLASSIFICATION_STATUS = {
    "job_selection_confirmed": "Selected",
    "offer_received": "Offer Received",
    "offer_accepted": "Offer Accepted",
    "offer_declined": "Offer Declined",
    "offer_revoked": "Offer Revoked",
    "joining_confirmed": "Joining Confirmed",
    "joining_date_updated": "Joining Confirmed",
    "onboarding_started": "Joining Confirmed",
    "background_verification": "Joining Confirmed",
    "document_verification": "HR Confirmation",
    "compensation_confirmation": "HR Confirmation",
    "final_round_cleared": "Final Round Cleared",
    "hr_confirmation": "HR Confirmation",
    "interview_update": "Interview In Progress",
    "interview_shortlisted": "Interview Shortlisted",
    "interview_confirmed": "Interview Confirmed",
    "interview_rescheduled": "Interview Rescheduled",
    "interview_cancelled": "Interview Cancelled",
    "candidate_rejected": "Rejected",
    "needs_review": "Needs Review",
    "not_relevant": "Profile Active",
}

_STATUS_RANK = {
    "Profile Active": 10, "Interview In Progress": 20,
    "Interview Confirmed": 25, "Interview Rescheduled": 25,
    "Interview Cancelled": 20,
    "Interview Shortlisted": 30, "Final Round Cleared": 35, "Rejected": 35, "Selected": 40,
    "HR Confirmation": 45,
    "Offer Received": 50, "Offer Accepted": 60, "Offer Declined": 65,
    "Offer Revoked": 65, "Joining Confirmed": 70,
    "Onboarding Started": 80, "Joined": 90, "Needs Review": 0,
}


def _id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_candidate_id(candidate_id: str) -> str:
    """Resolve a recruitment event to one strong, persisted person identity."""
    value = str(candidate_id or "")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_candidate_id FROM candidate_identity_links WHERE alias_candidate_id=%s",
            (value,),
        )
        row = cur.fetchone()
    if row and row[0]:
        return str(row[0])
    try:
        from features import candidate_store
        return candidate_store.canonical_candidate_identity_id(value)
    except Exception:
        return value


@contextmanager
def candidate_booking_lock(candidate_id: str):
    """Cross-process PostgreSQL lock for one candidate's booking transaction."""
    if not use_postgres():
        yield
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"ai-mail-booking:{candidate_id}",))
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"ai-mail-booking:{candidate_id}",))


def ensure_schema() -> None:
    if not use_postgres():
        return
    with get_connection() as conn, conn.cursor() as cur:
        migrations = Path(__file__).with_name("migrations")
        for migration in sorted(migrations.glob("*_recruitment_mail_*.sql")):
            cur.execute(migration.read_text(encoding="utf-8"))


def _rows(cur) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def mailbox_for_candidate(candidate_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM candidate_mailboxes WHERE candidate_id=%s ORDER BY created_at DESC LIMIT 1", (candidate_id,))
        rows = _rows(cur)
    return rows[0] if rows else None


def mailbox_for_candidates(candidate_ids: list[str]) -> dict[str, Any] | None:
    """Find the best single mailbox across legacy rows for one phone identity (backward-compat)."""
    ids = [str(value) for value in candidate_ids if value]
    if not ids:
        return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes WHERE candidate_id=ANY(%s)
               ORDER BY (connection_status='CONNECTED') DESC,
                        monitoring_enabled DESC,
                        (credential_ciphertext IS NOT NULL) DESC,
                        last_successful_sync_at DESC NULLS LAST,
                        updated_at DESC LIMIT 1""",
            (ids,),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def mailboxes_for_candidates(candidate_ids: list[str]) -> list[dict[str, Any]]:
    """Return ALL mailboxes across identity rows — supports multiple emails per candidate."""
    ids = [str(value) for value in candidate_ids if value]
    if not ids:
        return []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes WHERE candidate_id=ANY(%s)
               ORDER BY (connection_status='CONNECTED') DESC,
                        monitoring_enabled DESC,
                        (credential_ciphertext IS NOT NULL) DESC,
                        last_successful_sync_at DESC NULLS LAST,
                        updated_at DESC""",
            (ids,),
        )
        rows = _rows(cur)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("email_address") or "").strip().casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique


def mailbox_health_rows() -> list[dict[str, Any]]:
    """Return credential-free mailbox state for lightweight health polling."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.id,m.candidate_id,
                      COALESCE(l.canonical_candidate_id,m.candidate_id)
                        AS canonical_candidate_id,
                      m.email_address,m.connection_status,
                      m.monitoring_enabled,m.last_error_code,m.last_error_message,
                      m.last_successful_sync_at,m.updated_at
               FROM candidate_mailboxes m
               LEFT JOIN candidate_identity_links l
                 ON l.alias_candidate_id=m.candidate_id
               WHERE m.credential_ciphertext IS NOT NULL
                 AND m.connection_status <> 'SUPERSEDED'
               ORDER BY m.updated_at DESC""",
        )
        return _rows(cur)


def mailbox_overview_rows() -> list[dict[str, Any]]:
    """Return every active mailbox and its counters without exposing credentials.

    This is intentionally a bulk operation for the dashboard.  The old client
    loaded up to 500 candidates and then called the single-candidate mailbox
    endpoint once per candidate, which made the initial render depend on
    hundreds of HTTP round trips.
    """
    return [
        {"mailbox": mailbox, "stats": mailbox_stats(str(mailbox["id"]))}
        for mailbox in mailbox_health_rows()
    ]


def mailbox_by_id(mailbox_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM candidate_mailboxes WHERE id=%s", (mailbox_id,))
        rows = _rows(cur)
    return rows[0] if rows else None


def mailbox_by_email(email_address: str) -> dict[str, Any] | None:
    """Resolve a connected mailbox for a Gmail Pub/Sub emailAddress."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes
               WHERE lower(email_address)=lower(%s) AND monitoring_enabled=true
               ORDER BY (connection_status='CONNECTED') DESC, updated_at DESC LIMIT 1""",
            (str(email_address or "").strip(),),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def upsert_mailbox(candidate_id: str, email: str, **fields: Any) -> dict[str, Any]:
    mailbox_id = fields.pop("id", None) or _id()
    supplied_identity_ids = fields.pop("identity_ids", None)
    email = str(email or "").strip().lower()
    with get_connection() as conn, conn.cursor() as cur:
        # Reconnecting through a legacy candidate alias must reuse the Gmail
        # mailbox already attached to the same verified person identity.
        cur.execute(
            "SELECT canonical_candidate_id FROM candidate_identity_links WHERE alias_candidate_id=%s",
            (candidate_id,),
        )
        identity = cur.fetchone()
        canonical_id = str(identity[0]) if identity and identity[0] else str(candidate_id)
        cur.execute(
            "SELECT alias_candidate_id FROM candidate_identity_links WHERE canonical_candidate_id=%s",
            (canonical_id,),
        )
        identity_ids = {canonical_id, str(candidate_id), *(str(value[0]) for value in cur.fetchall())}
        if supplied_identity_ids:
            identity_ids.update(str(value) for value in supplied_identity_ids if value)
        cur.execute(
            """SELECT * FROM candidate_mailboxes
               WHERE candidate_id=ANY(%s) AND lower(email_address)=lower(%s)
               ORDER BY (connection_status='CONNECTED') DESC,
                        monitoring_enabled DESC,
                        (credential_ciphertext IS NOT NULL) DESC,
                        last_successful_sync_at DESC NULLS LAST,
                        updated_at DESC LIMIT 1 FOR UPDATE""",
            (list(identity_ids), email),
        )
        existing = _rows(cur)
        if existing:
            cur.execute(
                """UPDATE candidate_mailboxes SET monitoring_enabled=COALESCE(%s,monitoring_enabled),
                     connection_status=%s,
                     credential_ciphertext=COALESCE(%s,credential_ciphertext),
                     updated_at=now() WHERE id=%s RETURNING *""",
                (
                    fields.get("monitoring_enabled"),
                    fields.get("connection_status", "PENDING"),
                    fields.get("credential_ciphertext"),
                    existing[0]["id"],
                ),
            )
            return _rows(cur)[0]
        cur.execute("""
            INSERT INTO candidate_mailboxes(id,candidate_id,provider,email_address,connection_type,monitoring_enabled,connection_status,credential_ciphertext,created_at,updated_at)
            VALUES(%s,%s,'gmail',%s,'oauth2',%s,%s,%s,now(),now())
            ON CONFLICT(candidate_id, lower(email_address)) DO UPDATE SET
              monitoring_enabled=EXCLUDED.monitoring_enabled, connection_status=EXCLUDED.connection_status,
              credential_ciphertext=COALESCE(EXCLUDED.credential_ciphertext,candidate_mailboxes.credential_ciphertext), updated_at=now()
            RETURNING *
        """, (mailbox_id,candidate_id,email,bool(fields.get("monitoring_enabled",False)),fields.get("connection_status","PENDING"),fields.get("credential_ciphertext")))
        row=_rows(cur)[0]
        cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE lower(email_address)=lower(%s) AND NOT (candidate_id=ANY(%s)) LIMIT 1",(email,list(identity_ids)));duplicate=cur.fetchone()
        if duplicate:
            cur.execute("""INSERT INTO recruitment_review_flags(id,candidate_id,flag_type,severity,details,created_at)
              VALUES(%s,%s,'POSSIBLE_DUPLICATE_CANDIDATE','HIGH',%s::jsonb,now()) ON CONFLICT(candidate_id,event_id,flag_type) DO NOTHING""",(_id(),candidate_id,json.dumps({'matching_candidate_id':duplicate[0],'reason':'same_mailbox_email'})))
        return row


def supersede_duplicate_mailboxes(
    candidate_ids: list[str], email_address: str, keep_mailbox_id: str,
) -> int:
    """Disable stale copies of one mailbox across legacy rows for the same person."""
    ids = [str(value) for value in candidate_ids if value]
    if not ids:
        return 0
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE candidate_mailboxes
               SET monitoring_enabled=false,
                   connection_status='SUPERSEDED',
                   next_sync_at=NULL,
                   last_error_code=NULL,
                   last_error_message=NULL,
                   updated_at=now()
               WHERE candidate_id=ANY(%s)
                 AND lower(email_address)=lower(%s)
                 AND id<>%s""",
            (ids, str(email_address or "").strip(), keep_mailbox_id),
        )
        return int(cur.rowcount or 0)


def update_mailbox(mailbox_id: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed={"monitoring_enabled","connection_status","credential_ciphertext","sync_cursor","provider_history_id","last_sync_attempt_at","last_successful_sync_at","next_sync_at","failed_sync_count","last_error_code","last_error_message","gmail_watch_expiration","gmail_watch_topic","last_push_history_id"}
    clean={k:v for k,v in values.items() if k in allowed}
    if not clean:
        return mailbox_by_id(mailbox_id) or {}
    assignments=", ".join(f"{k}=%s" for k in clean)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE candidate_mailboxes SET {assignments},updated_at=now() WHERE id=%s RETURNING *", (*clean.values(),mailbox_id))
        rows=_rows(cur)
    return rows[0] if rows else {}


def stage_gmail_messages_and_advance_cursor(
    mailbox_id: str, refs: list[dict[str, Any]], cursor: str | None,
) -> int:
    """Atomically persist every discovered Gmail ID, then advance its cursor."""
    unique_ids = list(dict.fromkeys(str(ref.get("id") or "") for ref in refs if ref.get("id")))
    with get_connection() as conn, conn.cursor() as cur:
        inserted = 0
        for provider_message_id in unique_ids:
            cur.execute(
                """INSERT INTO gmail_message_ingestion_queue(
                       id,mailbox_id,provider_message_id,source_history_id,discovery_source,status,discovered_at,updated_at)
                   VALUES(%s,%s,%s,%s,'GMAIL_HISTORY','QUEUED',now(),now())
                   ON CONFLICT(mailbox_id,provider_message_id) DO NOTHING""",
                (_id(), mailbox_id, provider_message_id, cursor),
            )
            inserted += int(cur.rowcount or 0)
        cur.execute(
            """UPDATE candidate_mailboxes SET provider_history_id=%s,sync_cursor=%s,
                 updated_at=now() WHERE id=%s""",
            (cursor, cursor, mailbox_id),
        )
    return inserted


def stage_gmail_messages(
    mailbox_id: str, refs: list[dict[str, Any]], *, discovery_source: str = "RECOVERY_AUDIT",
    source_history_id: str | None = None,
) -> int:
    """Durably stage explicitly selected Gmail IDs without changing the cursor."""
    unique_ids = list(dict.fromkeys(str(ref.get("id") or "") for ref in refs if ref.get("id")))
    with get_connection() as conn, conn.cursor() as cur:
        inserted = 0
        for provider_message_id in unique_ids:
            cur.execute(
                """INSERT INTO gmail_message_ingestion_queue(
                       id,mailbox_id,provider_message_id,source_history_id,discovery_source,status,discovered_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,'QUEUED',now(),now())
                   ON CONFLICT(mailbox_id,provider_message_id) DO NOTHING""",
                (_id(), mailbox_id, provider_message_id, source_history_id, discovery_source),
            )
            inserted += int(cur.rowcount or 0)
    return inserted


def claim_gmail_ingestion(mailbox_id: str, *, limit: int) -> list[dict[str, Any]]:
    """Claim a bounded durable batch, recovering rows abandoned by a crash."""
    lease_minutes = max(1, min(30, int(os.getenv("AI_MAIL_INGESTION_LEASE_MINUTES", "5"))))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE gmail_message_ingestion_queue SET status='QUEUED',started_at=NULL,updated_at=now(),
                 last_error_message='Recovered after interrupted processing'
               WHERE mailbox_id=%s AND status='RUNNING'
                 AND updated_at<now()-(%s||' minutes')::interval""",
            (mailbox_id, lease_minutes),
        )
        cur.execute(
            """SELECT id FROM gmail_message_ingestion_queue
               WHERE mailbox_id=%s AND status='QUEUED'
               ORDER BY discovered_at,id FOR UPDATE SKIP LOCKED LIMIT %s""",
            (mailbox_id, max(1, min(int(limit), 500))),
        )
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            return []
        cur.execute(
            """UPDATE gmail_message_ingestion_queue SET status='RUNNING',attempts=attempts+1,
                 started_at=now(),updated_at=now() WHERE id=ANY(%s) RETURNING *""",
            (ids,),
        )
        rows = _rows(cur)
    order = {value: index for index, value in enumerate(ids)}
    return sorted(rows, key=lambda row: order[row["id"]])


def finish_gmail_ingestion(
    ingestion_id: str, *, status: str, error: Exception | None = None, max_attempts: int = 5,
) -> str:
    """Complete, tombstone, or safely requeue one staged Gmail message."""
    requested = str(status).upper()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT attempts FROM gmail_message_ingestion_queue WHERE id=%s FOR UPDATE", (ingestion_id,))
        row = cur.fetchone()
        attempts = int(row[0] if row else max_attempts)
        final = requested
        if requested == "QUEUED" and attempts >= max_attempts:
            final = "DEAD_LETTER"
        cur.execute(
            """UPDATE gmail_message_ingestion_queue SET status=%s,
                 completed_at=CASE WHEN %s IN ('COMPLETED','DELETED','DEAD_LETTER') THEN now() ELSE NULL END,
                 started_at=CASE WHEN %s='QUEUED' THEN NULL ELSE started_at END,
                 last_error_code=%s,last_error_message=%s,updated_at=now() WHERE id=%s""",
            (final, final, final, type(error).__name__ if error else None, str(error)[:400] if error else None, ingestion_id),
        )
    return final


def pending_gmail_ingestion_count(mailbox_id: str) -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM gmail_message_ingestion_queue WHERE mailbox_id=%s AND status IN ('QUEUED','RUNNING')",
            (mailbox_id,),
        )
        row = cur.fetchone()
    return int(row[0] if row else 0)


def record_pubsub_delivery(
    pubsub_message_id: str, *, subscription: str, email_address: str,
    history_id: str, mailbox_id: str | None, status: str,
    error_code: str | None = None,
) -> bool:
    """Persist the push envelope; False means Google retried an existing ID."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gmail_pubsub_deliveries(pubsub_message_id,subscription,email_address,
                 history_id,mailbox_id,delivery_status,error_code,received_at,processed_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,now(),CASE WHEN %s='QUEUED' THEN now() ELSE NULL END)
               ON CONFLICT(pubsub_message_id) DO NOTHING RETURNING pubsub_message_id""",
            (pubsub_message_id, subscription, email_address, history_id, mailbox_id, status, error_code, status),
        )
        return cur.fetchone() is not None


def enqueue_sync(mailbox_id: str, *, requested_by: str, scheduled_for: datetime | None=None) -> dict[str, Any]:
    job_id=_id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_sync_jobs WHERE mailbox_id=%s AND status IN('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(mailbox_id,))
        existing=_rows(cur) if cur.description else []
        if existing:return existing[0]
        cur.execute("""INSERT INTO mailbox_sync_jobs(id,mailbox_id,status,scheduled_for,requested_by,created_at)
          VALUES(%s,%s,'QUEUED',%s,%s,now()) RETURNING *""",(job_id,mailbox_id,scheduled_for or now(),requested_by))
        return _rows(cur)[0]


def enqueue_historical_rescan(mailbox_id: str, *, requested_by: str, range_start: date, range_end: date) -> dict[str, Any]:
    job_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_sync_jobs WHERE mailbox_id=%s AND job_type='HISTORICAL_RESCAN' AND status IN('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (mailbox_id,))
        existing = _rows(cur) if cur.description else []
        if existing:
            return existing[0]
        cur.execute("""INSERT INTO mailbox_sync_jobs(id,mailbox_id,status,scheduled_for,requested_by,job_type,range_start,range_end,created_at)
          VALUES(%s,%s,'QUEUED',now(),%s,'HISTORICAL_RESCAN',%s,%s,now()) RETURNING *""",
          (job_id, mailbox_id, requested_by, range_start, range_end))
        return _rows(cur)[0]


def claim_job() -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT j.id FROM mailbox_sync_jobs j WHERE j.status='QUEUED' AND j.scheduled_for<=now()
          AND NOT EXISTS(SELECT 1 FROM mailbox_sync_jobs active WHERE active.mailbox_id=j.mailbox_id AND active.status='RUNNING')
          ORDER BY j.scheduled_for FOR UPDATE OF j SKIP LOCKED LIMIT 1""")
        row=cur.fetchone()
        if not row:return None
        cur.execute("UPDATE mailbox_sync_jobs SET status='RUNNING',started_at=now(),attempts=attempts+1 WHERE id=%s RETURNING *",(row[0],))
        return _rows(cur)[0]


def recover_interrupted_jobs() -> int:
    """Requeue jobs that belonged to a previous backend process."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_sync_jobs SET status='QUEUED',scheduled_for=now(),
          started_at=NULL,completed_at=NULL,error_message='Backend restarted; job resumed automatically'
          WHERE status='RUNNING'""")
        return int(cur.rowcount or 0)


def finish_job(job_id: str, *, status: str, counts: dict[str,int]|None=None, error: str|None=None) -> None:
    c=counts or {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_sync_jobs SET status=%s,completed_at=now(),messages_fetched=%s,messages_processed=%s,
          events_detected=%s,error_message=%s WHERE id=%s""",(status,c.get('fetched',0),c.get('processed',0),c.get('events',0),error,job_id))


def retry_job(job_id:str,*,delay_minutes:int,error:str,max_attempts:int=5)->str:
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("SELECT attempts FROM mailbox_sync_jobs WHERE id=%s FOR UPDATE",(job_id,));row=cur.fetchone();attempts=int(row[0] if row else max_attempts)
        status='DEAD_LETTER' if attempts>=max_attempts else 'QUEUED'
        cur.execute("UPDATE mailbox_sync_jobs SET status=%s,scheduled_for=now()+(%s||' minutes')::interval,completed_at=CASE WHEN %s='DEAD_LETTER' THEN now() ELSE NULL END,error_message=%s WHERE id=%s",(status,delay_minutes,status,error,job_id))
    return status


def insert_message(mailbox: dict[str,Any], message: dict[str,Any], score: float) -> tuple[dict[str,Any],bool]:
    mid=_id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_messages(id,mailbox_id,candidate_id,provider_message_id,provider_thread_id,sender_name,sender_email,
          recipient_email,subject,sent_at,message_hash,body_hash,recruitment_relevance_score,processing_status,body_text,html_body_text,
          authentication_results,received_spf,rfc_message_id,message_direction,gmail_label_ids,to_metadata,
          reply_to_email,return_path_email,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'FILTERED',%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,now(),now())
          ON CONFLICT(mailbox_id,provider_message_id) DO UPDATE SET
            body_text=COALESCE(mailbox_messages.body_text,EXCLUDED.body_text),
            html_body_text=COALESCE(mailbox_messages.html_body_text,EXCLUDED.html_body_text),
            authentication_results=COALESCE(EXCLUDED.authentication_results,mailbox_messages.authentication_results),
            received_spf=COALESCE(EXCLUDED.received_spf,mailbox_messages.received_spf),
            reply_to_email=COALESCE(EXCLUDED.reply_to_email,mailbox_messages.reply_to_email),
            return_path_email=COALESCE(EXCLUDED.return_path_email,mailbox_messages.return_path_email),
            rfc_message_id=COALESCE(EXCLUDED.rfc_message_id,mailbox_messages.rfc_message_id),
            message_direction=COALESCE(EXCLUDED.message_direction,mailbox_messages.message_direction),
            gmail_label_ids=CASE WHEN EXCLUDED.gmail_label_ids<>'[]'::jsonb THEN EXCLUDED.gmail_label_ids ELSE mailbox_messages.gmail_label_ids END,
            to_metadata=CASE WHEN EXCLUDED.to_metadata<>'[]'::jsonb THEN EXCLUDED.to_metadata ELSE mailbox_messages.to_metadata END,
            recruitment_relevance_score=GREATEST(COALESCE(mailbox_messages.recruitment_relevance_score,0),EXCLUDED.recruitment_relevance_score),updated_at=now()
          RETURNING *, (xmax = 0) AS was_created""",
          (mid,mailbox['id'],mailbox['candidate_id'],message['provider_message_id'],message.get('provider_thread_id'),message.get('sender_name'),message.get('sender_email'),message.get('recipient_email'),message.get('subject'),message.get('sent_at'),message['message_hash'],message['body_hash'],score,message.get('body'),message.get('html_body'),message.get('authentication_results'),message.get('received_spf'),message.get('rfc_message_id'),message.get('message_direction'),json.dumps(message.get('gmail_label_ids') or []),json.dumps(message.get('to_metadata') or []),message.get('reply_to_email'),message.get('return_path_email')))
        rows=_rows(cur) if cur.description else []
    if not rows:return {},False
    row=rows[0];created=bool(row.pop('was_created',False));return row,created


def mark_reprocessed(message_id: str, previous_status: str | None, new_status: str, reason: str) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_messages SET previous_processing_status=%s,processing_status=%s,
          reprocessed_at=now(),reprocessing_reason=%s,reprocessing_prompt_version='recruitment_email_status_extraction_v3',
          semantic_classifier_version='v3',updated_at=now()
          WHERE id=%s""", (previous_status,new_status,reason,message_id))


def stored_message(mailbox_id: str, provider_message_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_messages WHERE mailbox_id=%s AND provider_message_id=%s", (mailbox_id,provider_message_id))
        rows=_rows(cur)
    return rows[0] if rows else None


def _max_ai_attempts() -> int:
    """Attempts before a message is parked terminally rather than requeued."""
    try:
        return max(3, min(50, int(os.getenv("AI_MAIL_MAX_AI_ATTEMPTS", "12"))))
    except (TypeError, ValueError):
        return 12


def claim_ai_messages(limit: int = 1, *, lease_seconds: int = 150) -> list[dict[str, Any]]:
    """Lease queued semantic work so crashes/timeouts cannot lose or duplicate it."""
    with get_connection() as conn, conn.cursor() as cur:
        # A row that has burned through its attempts is parked terminally
        # rather than returned to the queue: without this a message can be
        # reclaimed indefinitely (production reached 105 attempts). Terminal
        # rows are excluded from the claim below, and no backoff can revive
        # them, so they stop consuming Ollama capacity while staying visible.
        max_attempts=_max_ai_attempts()
        cur.execute("""UPDATE mailbox_messages SET processing_status='AI_FAILED_TERMINAL',
              ai_lease_expires_at=NULL,updated_at=now(),ai_last_error_code='MAX_ATTEMPTS_EXHAUSTED'
            WHERE processing_status='AI_RUNNING' AND ai_lease_expires_at<now()
              AND COALESCE(ai_retry_count,0)>=%s""",(max_attempts,))
        cur.execute("""UPDATE mailbox_messages SET processing_status='AI_QUEUED',ai_lease_expires_at=NULL,
              updated_at=now(),ai_last_error_code='LEASE_EXPIRED'
            WHERE processing_status='AI_RUNNING' AND ai_lease_expires_at<now()""")
        # Rows that exhausted their attempts before the cap existed are already
        # excluded from the claim below, but would otherwise sit in the queue
        # forever looking like a live backlog. Park them under the same
        # terminal state so the queue reflects what is actually claimable.
        cur.execute("""UPDATE mailbox_messages SET processing_status='AI_FAILED_TERMINAL',
              ai_lease_expires_at=NULL,updated_at=now(),ai_last_error_code='MAX_ATTEMPTS_EXHAUSTED'
            WHERE processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
              AND COALESCE(ai_retry_count,0)>=%s""",(max_attempts,))
        cur.execute("""SELECT id FROM mailbox_messages
          WHERE processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
            AND COALESCE(ai_retry_after,now())<=now()
            AND COALESCE(ai_retry_count,0)<%s
          ORDER BY sent_at ASC,id FOR UPDATE SKIP LOCKED LIMIT %s""",(max_attempts,max(1,min(limit,20))))
        ids=[row[0] for row in cur.fetchall()]
        if not ids:return []
        cur.execute("""UPDATE mailbox_messages m SET processing_status='AI_RUNNING',
              ai_lease_expires_at=now()+(%s||' seconds')::interval,updated_at=now()
            FROM candidate_mailboxes b WHERE m.id=ANY(%s) AND b.id=m.mailbox_id
            RETURNING m.*,b.email_address,b.candidate_id AS mailbox_candidate_id""",
            (max(30,min(int(lease_seconds),900)),ids))
        rows=_rows(cur)
    for row in rows:
        row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['id'],include_text=True)]
    return rows


def retry_pending_messages(limit: int = 20) -> list[dict[str, Any]]:
    """Compatibility read used by diagnostics; workers should claim leases."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m.*,b.email_address,b.candidate_id AS mailbox_candidate_id
          FROM mailbox_messages m JOIN candidate_mailboxes b ON b.id=m.mailbox_id
          WHERE m.processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
            AND COALESCE(m.ai_retry_after,now())<=now()
          ORDER BY m.sent_at ASC LIMIT %s""",(max(1,min(limit,100)),))
        rows=_rows(cur)
    for row in rows:
        row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['id'],include_text=True)]
    return rows


def schedule_ai_retry(message_id: str, *, succeeded: bool) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        if succeeded:
            cur.execute("UPDATE mailbox_messages SET ai_retry_after=NULL,ai_lease_expires_at=NULL,ai_last_error_code=NULL,updated_at=now() WHERE id=%s",(message_id,))
        else:
            cur.execute("""UPDATE mailbox_messages SET ai_retry_count=ai_retry_count+1,
              ai_retry_after=now()+(LEAST(360,POWER(2,LEAST(ai_retry_count+1,8)))||' minutes')::interval,
              processing_status='AI_RETRY_PENDING',ai_lease_expires_at=NULL,
              updated_at=now() WHERE id=%s""",(message_id,))


def is_duplicate_content(candidate_id:str,message_id:str,message_hash:str,body_hash:str,subject:str|None=None)->bool:
    """Has this candidate already had an event from an identical message?

    Two independent tests, and the difference matters:

    ``message_hash`` covers sender + subject + sent_at, so it identifies the
    same message arriving twice. That test is exact and is left alone.

    ``body_hash`` is the looser one, and on its own it is too loose. A recruiter
    who books two different interviews from the same template sends two mails
    whose bodies are byte-identical — the substance lives in the subject and in
    the invitation, not the covering note. That happened: two Sourcebae invites
    for the same candidate shared body hash b039d324…, so the second interview
    was dropped as a resend of the first and never reached booking at all.

    So a body match now also requires the subject to match. A genuine resend
    still has both; two different interviews do not.
    """
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM mailbox_messages m JOIN ai_recruitment_events e ON e.mailbox_message_id=m.id
          WHERE m.candidate_id=%s AND m.id<>%s AND (
                m.message_hash=%s
                OR (m.body_hash=%s AND %s<>%s
                    AND COALESCE(m.subject,'')=COALESCE(%s,''))
          ) LIMIT 1""",(candidate_id,message_id,message_hash,body_hash,body_hash,content_hash_empty(),subject))
        return cur.fetchone() is not None


def content_hash_empty()->str:
    import hashlib
    return hashlib.sha256(b'').hexdigest()


def mark_message_status(message_id:str,status:str,*,reason:str|None=None,cleanup_version:str|None=None,
                        error_code:str|None=None)->None:
    """Set the message's processing state.

    ``error_code`` also writes ``ai_last_error_code``, which is the field the
    queue is diagnosed from — ``ignore_reason`` keeps older text and has already
    misled one investigation. A retry parked without a live code looks like an
    unexplained backlog: 24 messages sat on OLLAMA_SCHEMA_VALIDATION_FAILED with
    a null code and nothing said why.
    """
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_messages SET processing_status=%s,ignore_reason=%s,
          ignored_at=CASE WHEN %s LIKE 'IGNORED%%' THEN now() ELSE ignored_at END,
          cleanup_version=COALESCE(%s,cleanup_version),semantic_classifier_version='v3',
          ai_last_error_code=COALESCE(%s,ai_last_error_code),
          ai_lease_expires_at=CASE WHEN %s='AI_RUNNING' THEN ai_lease_expires_at ELSE NULL END,updated_at=now() WHERE id=%s""",
          (status,reason,status,cleanup_version,error_code,status,message_id))


# Only genuine offer documents identify a duplicate OFFER. Recurring documents
# such as a resume, or a new interview invite, must never suppress a distinct
# event just because the same file was attached to an earlier email.
_OFFER_DOCUMENT_TYPES = ("OFFER_LETTER", "APPOINTMENT_LETTER", "JOINING_LETTER", "COMPENSATION_BREAKUP")


def is_duplicate_offer_attachment(candidate_id:str,message_id:str)->bool:
    """True when an OFFER-document checksum already belongs to a visible event for
    this candidate (scoped to offer documents so a recurring resume or a new
    interview invitation never suppresses a distinct event)."""
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM mailbox_attachments current_attachment
          JOIN mailbox_attachments previous_attachment ON previous_attachment.checksum=current_attachment.checksum
            AND previous_attachment.mailbox_message_id<>current_attachment.mailbox_message_id
          JOIN ai_recruitment_events e ON e.mailbox_message_id=previous_attachment.mailbox_message_id
          WHERE current_attachment.mailbox_message_id=%s AND e.candidate_id=%s
            AND current_attachment.attachment_type = ANY(%s)
            AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE') LIMIT 1""",(message_id,candidate_id,list(_OFFER_DOCUMENT_TYPES)))
        return cur.fetchone() is not None


def is_duplicate_thread_status(candidate_id:str,message_id:str,status:str)->bool:
    """Suppress repeated reminders with the same status in the same Gmail thread."""
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM mailbox_messages current_message
          JOIN mailbox_messages previous_message ON previous_message.provider_thread_id=current_message.provider_thread_id
            AND previous_message.id<>current_message.id
          JOIN ai_recruitment_events e ON e.mailbox_message_id=previous_message.id
          WHERE current_message.id=%s AND current_message.provider_thread_id IS NOT NULL
            AND e.candidate_id=%s AND e.primary_status=%s
            AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE') LIMIT 1""",(message_id,candidate_id,status))
        return cur.fetchone() is not None


def attachment_cache(checksum: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_attachment_cache WHERE checksum=%s", (checksum,))
        rows = _rows(cur)
    return rows[0] if rows else None


def save_attachment(message_id: str, attachment: dict[str, Any]) -> dict[str, Any]:
    aid = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_attachment_cache(checksum,mime_type,size,extracted_text,extraction_method,attachment_type,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,now(),now()) ON CONFLICT(checksum) DO UPDATE SET
          extracted_text=CASE WHEN length(COALESCE(EXCLUDED.extracted_text,''))>0 THEN EXCLUDED.extracted_text ELSE mailbox_attachment_cache.extracted_text END,
          extraction_method=CASE WHEN length(COALESCE(EXCLUDED.extracted_text,''))>0 THEN EXCLUDED.extraction_method ELSE mailbox_attachment_cache.extraction_method END,
          attachment_type=CASE WHEN length(COALESCE(EXCLUDED.extracted_text,''))>0 THEN EXCLUDED.attachment_type ELSE mailbox_attachment_cache.attachment_type END,
          updated_at=now()""",
          (attachment['checksum'],attachment.get('mime_type'),attachment.get('size'),attachment.get('text'),attachment.get('extraction_method'),attachment.get('attachment_type')))
        cur.execute("""INSERT INTO mailbox_attachments(id,mailbox_message_id,filename,mime_type,size,checksum,attachment_type,extraction_status,extracted_text_reference,created_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(mailbox_message_id,checksum) DO UPDATE SET
          filename=EXCLUDED.filename,mime_type=EXCLUDED.mime_type,size=EXCLUDED.size,
          attachment_type=EXCLUDED.attachment_type,extraction_status=EXCLUDED.extraction_status RETURNING *""",
          (aid,message_id,attachment.get('filename'),attachment.get('mime_type'),attachment.get('size'),attachment['checksum'],attachment.get('attachment_type'),attachment.get('extraction_status'),attachment['checksum']))
        return _rows(cur)[0]


def attachments_for_message(message_id: str, *, include_text: bool=False) -> list[dict[str,Any]]:
    fields="a.*,c.extraction_method"+(",c.extracted_text" if include_text else "")
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"SELECT {fields} FROM mailbox_attachments a LEFT JOIN mailbox_attachment_cache c ON c.checksum=a.checksum WHERE a.mailbox_message_id=%s ORDER BY a.created_at",(message_id,));return _rows(cur)


_INTERVIEW_CLASSIFICATIONS = {"interview_confirmed", "interview_cancelled", "interview_rescheduled"}

# One interview is described by several mails, so the lookup has to reach back
# far enough to find the first of them without trawling a candidate's history.
_CALENDAR_LOOKBACK_DAYS = 30


def _interview_identity(result: dict[str,Any]) -> dict[str,Any]:
    interview=result.get('interview') or {}; recruiter=result.get('recruiter') or {}; company=result.get('company') or {}
    return {
        'interview_date': interview.get('date'), 'interview_time': interview.get('time'),
        'recruiter_email': recruiter.get('email'), 'company_domain': company.get('domain'),
        'calendar_uid': result.get('calendar_uid'), 'calendar_sequence': result.get('calendar_sequence'),
    }


def existing_interview_event(candidate_id: str, result: dict[str,Any]) -> dict[str,Any] | None:
    """The event this interview mail repeats, if one is already recorded.

    Only interviews are considered. An offer letter and its covering note are a
    different problem with different rules, and collapsing them here would be a
    silent behaviour change to a path nobody asked about.
    """
    from features import interview_event_identity

    if str(result.get('classification') or '').strip().lower() not in _INTERVIEW_CLASSIFICATIONS:
        return None
    incoming=_interview_identity(result)
    # Nothing to match on: no calendar identity and no schedule.
    if not incoming['calendar_uid'] and not (incoming['interview_date'] and incoming['interview_time']):
        return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM ai_recruitment_events
          WHERE candidate_id=%s AND created_at > now() - (%s || ' days')::interval
          ORDER BY created_at DESC LIMIT 100""",(candidate_id,str(_CALENDAR_LOOKBACK_DAYS)))
        rows=_rows(cur)
    return interview_event_identity.duplicate_of(rows,incoming)


def attach_calendar_identity(event_id: str, uid: Any, sequence: Any) -> None:
    """Record the calendar's identity on an event that was created without it.

    The covering note is classified by AI and has no UID; the invitation that
    follows does. Writing it onto the existing row is what lets the *next*
    delivery — a resend, or Google's second copy — be recognised by UID rather
    than by schedule.
    """
    if not uid:
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE ai_recruitment_events SET calendar_uid=%s,calendar_sequence=%s,updated_at=now()
          WHERE id=%s AND calendar_uid IS NULL""",(str(uid),sequence,event_id))


_CLOCK_IN_TEXT = re.compile(r"(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\s*([AP]M)?", re.I)


def typed_or_null(value: Any) -> Any:
    """Blank -> NULL for a column Postgres types as date, time or number.

    The model expresses "no value" both ways: sometimes ``null``, sometimes an
    empty string. An empty string reaching a typed column raises
    InvalidDatetimeFormat and aborts the whole INSERT.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def storable_time(value: Any) -> Any:
    """A value Postgres can store in a `time` column, or NULL.

    Nothing is lost by nulling this: the model's exact answer is kept verbatim
    in `structured_result`, and this column is only the projection the roster
    and audit read. What *is* lost by passing a bad value through is the entire
    event — the INSERT aborts, and because a raw psycopg2 error is not an
    AIGatewayError it never reaches the semantic retry path, so no code is
    recorded and the mail fails identically forever.

    Two Production cancellation mails died on `"15:30 - 16:00 IST"`: a range
    with a zone suffix, which Postgres reads as a timezone displacement. The
    start of a stated range is the interview time, so it is taken; anything with
    no readable clock at all becomes NULL.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    hit = _CLOCK_IN_TEXT.search(text)
    if not hit:
        return None
    hour, minute = int(hit.group(1)), hit.group(2)
    second = hit.group(3) or "00"
    meridiem = (hit.group(4) or "").upper()
    if meridiem == "AM" and hour == 12:
        hour = 0
    elif meridiem == "PM" and hour != 12:
        hour += 12
    if not 0 <= hour <= 23:
        return None
    return f"{hour:02d}:{minute}:{second}"


def storable_date(value: Any) -> Any:
    """A value Postgres can store in a `date` column, or NULL.

    Same contract as `storable_time`: the raw answer survives in
    `structured_result`, so a value this cannot represent is dropped from the
    projection rather than allowed to abort the event.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def storable_number(value: Any) -> Any:
    """A value Postgres can store in a `numeric` column, or NULL."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def create_event(candidate_id: str, message_id: str, result: dict[str,Any], *, model: str, duration_ms: int) -> dict[str,Any]:
    # One interview, one event. The recruiter's covering note and the calendar
    # invitation arrive a minute apart describing the same meeting, and neither
    # message_hash nor the subject-scoped body_hash dedupe can relate them —
    # they differ in both. Returning the event already recorded keeps a single
    # row, a single notification and a single booking attempt.
    already=existing_interview_event(candidate_id,result)
    if already:
        attach_calendar_identity(already['id'],result.get('calendar_uid'),result.get('calendar_sequence'))
        return already
    event_id=_id(); interview=result.get('interview') or {}; offer=result.get('offer') or {}; company=result.get('company') or {}; job=result.get('job') or {}; recruiter=result.get('recruiter') or {}
    validation_status=str(result.get('validation_status') or 'NEEDS_REVIEW').upper()
    review_state='AUTO_VALIDATED' if validation_status=='AUTO_VALIDATED' else 'PENDING'
    candidate_event={"primary_status":result.get("primary_status"),"confidence":result.get("confidence"),"structured_result":result,"review_status":review_state,"validation_status":validation_status,"visible_in_offer_review":True}
    visible=should_show_in_selection_offer_review(candidate_event)
    original_status=result.get('primary_status')
    status=original_status if visible else ('IGNORED_LOW_CONFIDENCE' if float(result.get('confidence') or 0)<.8 else 'IGNORED_NOT_OFFER_RELATED')
    review_status=review_state if visible else 'IGNORED'
    ignore_reason=None if visible else (result.get('ignore_reason') or 'LOW_CONFIDENCE_OR_NO_STRONG_OFFER_EVIDENCE')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO ai_recruitment_events(id,candidate_id,mailbox_message_id,primary_status,confidence,company_name,company_domain,job_title,
          recruiter_name,recruiter_email,interview_date,interview_time,interview_mode,offered_ctc,currency,joining_date,offer_date,offer_expiry_date,
          structured_result,summary,requires_manual_review,review_status,visible_in_offer_review,original_primary_status,ignore_reason,ignored_at,
          ai_model,prompt_name,prompt_version,schema_version,processing_duration_ms,calendar_uid,calendar_sequence,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,
            CASE WHEN %s THEN NULL ELSE now() END,%s,'recruitment_email_status_extraction_v3','v3','selection_offer_event_v1',%s,%s,%s,now(),now()) RETURNING *""",
          (event_id,candidate_id,message_id,status,result['confidence'],company.get('name'),company.get('domain'),job.get('title'),recruiter.get('name'),recruiter.get('email'),storable_date(interview.get('date')),storable_time(interview.get('time')),interview.get('mode'),storable_number(offer.get('offered_ctc')),offer.get('currency'),storable_date(offer.get('joining_date')),storable_date(offer.get('offer_date')),storable_date(offer.get('offer_expiry_date')),json.dumps(result),result.get('summary'),bool(result.get('requires_manual_review')) if visible else False,review_status,visible,original_status if not visible else None,ignore_reason,visible,model,duration_ms,(str(result.get('calendar_uid')) if result.get('calendar_uid') else None),result.get('calendar_sequence')))
        event=_rows(cur)[0]
        canonical_id=canonical_candidate_id(candidate_id)
        cur.execute("""UPDATE ai_recruitment_events SET canonical_candidate_id=%s,validation_status=%s,
          ai_status=%s,email_intent=%s,document_type=%s,evidence_summary=%s,event_fingerprint=%s
          WHERE id=%s RETURNING *""",(
          canonical_id,validation_status,str(result.get('ai_status') or 'ANALYZED'),
          result.get('email_intent'),result.get('document_type'),result.get('evidence_summary') or result.get('summary'),
          message_id,event_id))
        event=_rows(cur)[0]
        cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at)
          VALUES(%s,'system','system','AI_RECRUITMENT_EVENT_CREATED',%s,%s,%s::jsonb,now())""",(_id(),candidate_id,event_id,json.dumps({'primary_status':result['primary_status'],'confidence':result['confidence'],'model':model})))
        # Canonical candidate status and history are applied after the event is
        # committed by finalize_detection(), which enforces confidence and
        # monotonic transition rules in one place.
        from services.recruitment_mail_agent import OFFER_CASE_STATUSES
        if visible and result['primary_status'] in OFFER_CASE_STATUSES:
            cur.execute("""SELECT m.provider_thread_id,a.checksum FROM mailbox_messages m
              LEFT JOIN mailbox_attachments a ON a.mailbox_message_id=m.id WHERE m.id=%s
              ORDER BY CASE WHEN lower(COALESCE(a.filename,'')) ~ '(offer|appointment|joining)' THEN 0 ELSE 1 END LIMIT 1""",(message_id,))
            source=cur.fetchone() or (None,None)
            identity=source[1] or source[0] or '|'.join(str(value or '').strip().lower() for value in (company.get('name'),job.get('title')))
            raw_key='|'.join(str(value or '').strip().lower() for value in (candidate_id,identity))
            # An unknown offer must not collapse every offer for the candidate
            # into one case. PostgreSQL permits multiple NULL values in this
            # unique index, so only deduplicate when a stable identity exists.
            offer_case_key=__import__('hashlib').sha256(raw_key.encode()).hexdigest() if identity else None
            cur.execute("""INSERT INTO offer_verification_cases(id,candidate_id,ai_recruitment_event_id,offer_case_key,company_name,job_title,offered_ctc,currency,offer_date,joining_date,offer_expiry_date,verification_status,confidence,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_REVIEW',%s,now(),now())
              ON CONFLICT(offer_case_key) DO UPDATE SET ai_recruitment_event_id=EXCLUDED.ai_recruitment_event_id,
                company_name=COALESCE(EXCLUDED.company_name,offer_verification_cases.company_name),
                job_title=COALESCE(EXCLUDED.job_title,offer_verification_cases.job_title),
                offered_ctc=COALESCE(EXCLUDED.offered_ctc,offer_verification_cases.offered_ctc),
                currency=COALESCE(EXCLUDED.currency,offer_verification_cases.currency),
                offer_date=COALESCE(EXCLUDED.offer_date,offer_verification_cases.offer_date),
                joining_date=COALESCE(EXCLUDED.joining_date,offer_verification_cases.joining_date),
                offer_expiry_date=COALESCE(EXCLUDED.offer_expiry_date,offer_verification_cases.offer_expiry_date),
                confidence=GREATEST(offer_verification_cases.confidence,EXCLUDED.confidence),updated_at=now()""",
              (_id(),candidate_id,event_id,offer_case_key,company.get('name'),job.get('title'),storable_number(offer.get('offered_ctc')),offer.get('currency'),storable_date(offer.get('offer_date')),storable_date(offer.get('joining_date')),storable_date(offer.get('offer_expiry_date')),result['confidence']))
            cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at)
              VALUES(%s,'system','system','OFFER_CASE_CREATED',%s,%s,%s::jsonb,now())""",(_id(),candidate_id,event_id,json.dumps({'status':result['primary_status'],'confidence':result['confidence']})))
        cur.execute("SELECT confirmed_status FROM candidate_status_history WHERE candidate_id=%s AND confirmed_status IS NOT NULL ORDER BY reviewed_at DESC LIMIT 1",(candidate_id,));confirmed=cur.fetchone()
        conflict_pairs={('INTERVIEW_CANCELLED','INTERVIEW_RESCHEDULED'),('REJECTED','SELECTED'),('APPLICATION_WITHDRAWN','OFFER_LETTER_RECEIVED')}
        if confirmed and (confirmed[0],result['primary_status']) in conflict_pairs:
            cur.execute("""INSERT INTO recruitment_review_flags(id,candidate_id,event_id,flag_type,severity,details,created_at)
              VALUES(%s,%s,%s,'POTENTIAL_STATUS_CONFLICT','HIGH',%s::jsonb,now()) ON CONFLICT(candidate_id,event_id,flag_type) DO NOTHING""",(_id(),candidate_id,event_id,json.dumps({'confirmed_status':confirmed[0],'detected_status':result['primary_status']})))
        if offer.get('offered_ctc'):
            cur.execute("SELECT offered_ctc FROM offer_verification_cases WHERE candidate_id=%s AND ai_recruitment_event_id<>%s AND offered_ctc IS NOT NULL ORDER BY created_at DESC LIMIT 1",(candidate_id,event_id));previous_offer=cur.fetchone()
            if previous_offer and float(previous_offer[0])!=float(offer['offered_ctc']):
                cur.execute("""INSERT INTO recruitment_review_flags(id,candidate_id,event_id,flag_type,severity,details,created_at)
                  VALUES(%s,%s,%s,'POTENTIAL_OFFER_CONFLICT','HIGH',%s::jsonb,now()) ON CONFLICT(candidate_id,event_id,flag_type) DO NOTHING""",(_id(),candidate_id,event_id,json.dumps({'previous_ctc':float(previous_offer[0]),'detected_ctc':offer['offered_ctc']})))
        cur.execute("""UPDATE mailbox_messages SET processing_status=%s,ignore_reason=%s,
          ignored_at=CASE WHEN %s THEN ignored_at ELSE now() END,semantic_classifier_version='v3',updated_at=now() WHERE id=%s""",
          ('EVENT_CREATED' if visible else status,ignore_reason,visible,message_id))
    return finalize_detection(event, result=result, model=model, duration_ms=duration_ms)


def create_or_reprocess_event(candidate_id: str, message_id: str, result: dict[str,Any], *, model: str, duration_ms: int, reason: str) -> dict[str,Any]:
    """Update the existing event in place, preserving its prior classification in audit."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai_recruitment_events WHERE mailbox_message_id=%s", (message_id,))
        existing_rows=_rows(cur)
    if not existing_rows:
        event=create_event(candidate_id,message_id,result,model=model,duration_ms=duration_ms)
        audit(actor='system',role='system',action='HISTORICAL_EMAIL_REPROCESSED',candidate_id=candidate_id,source_id=event['id'],previous=None,new={'new_classification':result['primary_status'],'prompt_version':'v3','reason':reason})
        return event
    previous=existing_rows[0];company=result.get('company') or {};job=result.get('job') or {};offer=result.get('offer') or {};recruiter=result.get('recruiter') or {}
    with get_connection() as conn,conn.cursor() as cur:
        validation_status=str(result.get('validation_status') or 'NEEDS_REVIEW').upper()
        review_state='AUTO_VALIDATED' if validation_status=='AUTO_VALIDATED' else 'PENDING'
        cur.execute("""UPDATE ai_recruitment_events SET original_primary_status=COALESCE(original_primary_status,primary_status),
          primary_status=%s,confidence=%s,company_name=%s,company_domain=%s,job_title=%s,recruiter_name=%s,recruiter_email=%s,
          joining_date=%s,structured_result=%s::jsonb,summary=%s,requires_manual_review=%s,review_status=%s,
          visible_in_offer_review=true,ignore_reason=NULL,ignored_at=NULL,cleanup_version=NULL,ai_model=%s,
          prompt_name='recruitment_email_status_extraction_v3',prompt_version='v4',processing_duration_ms=%s,
          canonical_candidate_id=%s,validation_status=%s,ai_status=%s,email_intent=%s,document_type=%s,
          evidence_summary=%s,event_fingerprint=%s,updated_at=now()
          WHERE id=%s RETURNING *""",
          (result['primary_status'],result['confidence'],company.get('name'),company.get('domain'),job.get('title'),recruiter.get('name'),recruiter.get('email'),storable_date(offer.get('joining_date')),json.dumps(result),result.get('summary'),bool(result.get('requires_manual_review')),review_state,model,duration_ms,canonical_candidate_id(candidate_id),validation_status,str(result.get('ai_status') or 'ANALYZED'),result.get('email_intent'),result.get('document_type'),result.get('evidence_summary') or result.get('summary'),message_id,previous['id']))
        event=_rows(cur)[0]
        if result['primary_status'] in __import__('services.recruitment_mail_agent',fromlist=['OFFER_CASE_STATUSES']).OFFER_CASE_STATUSES:
            cur.execute("""INSERT INTO offer_verification_cases(id,candidate_id,ai_recruitment_event_id,company_name,job_title,joining_date,verification_status,confidence,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,'PENDING_REVIEW',%s,now(),now())
              ON CONFLICT(ai_recruitment_event_id) DO UPDATE SET company_name=EXCLUDED.company_name,job_title=EXCLUDED.job_title,
                joining_date=EXCLUDED.joining_date,verification_status='PENDING_REVIEW',confidence=EXCLUDED.confidence,updated_at=now()""",
              (_id(),candidate_id,event['id'],company.get('name'),job.get('title'),storable_date(offer.get('joining_date')),result['confidence']))
        cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
          VALUES(%s,'system','system','HISTORICAL_EMAIL_RECLASSIFIED',%s,%s,%s::jsonb,%s::jsonb,now())""",
          (_id(),candidate_id,event['id'],json.dumps({'classification':previous.get('primary_status'),'prompt_version':previous.get('prompt_version')},default=str),json.dumps({'classification':result['primary_status'],'prompt_version':'v3','reason':reason},default=str)))
    return finalize_detection(event, result=result, model=model, duration_ms=duration_ms)


def archive_event_for_message(message_id: str, *, status: str, reason: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Audit-safely remove a historical false positive from every consumer."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai_recruitment_events WHERE mailbox_message_id=%s FOR UPDATE", (message_id,))
        rows = _rows(cur)
        if not rows:
            return None
        previous = rows[0]
        structured = json.dumps(result) if result is not None else json.dumps(previous.get("structured_result") or {})
        cur.execute("""UPDATE ai_recruitment_events SET
          original_primary_status=COALESCE(original_primary_status,primary_status),primary_status=%s,
          structured_result=%s::jsonb,visible_in_offer_review=false,review_status='IGNORED',
          requires_manual_review=false,ignore_reason=%s,ignored_at=now(),cleanup_version='semantic_v4',
          validation_status='REJECTED',ai_status=COALESCE(ai_status,'ANALYZED'),
          prompt_name='recruitment_email_status_extraction_v3',prompt_version='v3',updated_at=now()
          WHERE id=%s RETURNING *""", (status, structured, reason, previous["id"]))
        archived = _rows(cur)[0]
        cur.execute("UPDATE offer_verification_cases SET verification_status='IGNORED',updated_at=now() WHERE ai_recruitment_event_id=%s", (previous["id"],))
        cur.execute("""UPDATE mail_monitoring_notifications SET dismissed_at=COALESCE(dismissed_at,now()),
          is_reviewed=true,reviewed_at=COALESCE(reviewed_at,now()),reviewed_by=COALESCE(reviewed_by,'system'),
          review_notes=COALESCE(review_notes,%s),is_false_detection=true,updated_at=now()
          WHERE ai_recruitment_event_id=%s""", (f"Automatically archived: {reason}", previous["id"]))
        # Preserve administrator-confirmed history; remove only unconfirmed AI
        # history that was generated by the now-archived false positive.
        cur.execute("DELETE FROM candidate_status_history WHERE source_id=%s AND confirmed_status IS NULL", (previous["id"],))
        cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
          VALUES(%s,'system','system','HISTORICAL_EVENT_ARCHIVED',%s,%s,%s::jsonb,%s::jsonb,now())""",
          (_id(), previous["candidate_id"], previous["id"],
           json.dumps({"classification": previous.get("primary_status"), "visible": previous.get("visible_in_offer_review")}, default=str),
           json.dumps({"classification": status, "visible": False, "reason": reason}, default=str)))
        return archived


def audit(*,actor:str,role:str,action:str,candidate_id:str|None=None,source_id:str|None=None,previous:Any=None,new:Any=None,source_ip:str|None=None)->None:
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,source_ip,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now())",(_id(),actor,role,action,candidate_id,source_id,json.dumps(previous) if previous is not None else None,json.dumps(new) if new is not None else None,source_ip))


def list_flags(*,status:str|None=None,limit:int=100)->list[dict[str,Any]]:
    where=' WHERE review_status=%s' if status else ''
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"SELECT * FROM recruitment_review_flags{where} ORDER BY created_at DESC LIMIT %s",(([status] if status else [])+[limit]));return _rows(cur)


def candidate_filter_ids(value:str)->set[str]:
    key=(value or '').strip().upper()
    with get_connection() as conn,conn.cursor() as cur:
        if key=='MAILBOX_CONNECTED':cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE connection_status='CONNECTED'")
        elif key=='MAILBOX_MONITORING_ENABLED':cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE monitoring_enabled=true")
        elif key=='MAILBOX_SYNC_FAILED':cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE connection_status='ERROR'")
        elif key=='PENDING_AI_REVIEW':
            predicate,params=qualified_event_sql('e');cur.execute(f"SELECT DISTINCT candidate_id FROM ai_recruitment_events e WHERE e.review_status='PENDING' AND {predicate}",params)
        elif key=='POTENTIAL_STATUS_CONFLICT':cur.execute("SELECT DISTINCT candidate_id FROM recruitment_review_flags WHERE flag_type LIKE 'POTENTIAL%%' AND review_status='PENDING'")
        elif key=='OFFER_VERIFIED':cur.execute("SELECT DISTINCT candidate_id FROM offer_verification_cases WHERE verification_status='VERIFIED'")
        else:cur.execute("SELECT DISTINCT candidate_id FROM ai_recruitment_events WHERE primary_status=%s",(key,))
        return {str(row[0]) for row in cur.fetchall()}


def list_events(*, candidate_id: str|None=None, review_status: str|None=None, limit:int=50, offset:int=0, active_only:bool=True) -> list[dict[str,Any]]:
    where=[]; params=[]
    if active_only:
        predicate,predicate_params=qualified_event_sql('e');where.append(predicate);params.extend(predicate_params)
    if candidate_id:where.append('e.candidate_id=%s');params.append(candidate_id)
    if review_status:where.append('e.review_status=%s');params.append(review_status)
    sql='''SELECT e.*,m.subject,m.sender_name,m.sender_email,m.sent_at AS email_sent_at,
      booking.booking_id,booking.booking_status,booking.failure_code AS booking_failure_code
      FROM ai_recruitment_events e
      LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
      LEFT JOIN LATERAL (
        SELECT a.booking_id,a.booking_status,a.failure_code
        FROM interview_auto_booking_audit a
        WHERE a.gmail_message_id=m.provider_message_id
        ORDER BY a.created_at DESC LIMIT 1
      ) booking ON true'''+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY e.created_at DESC LIMIT %s OFFSET %s';params.extend([limit,offset])
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql,params);rows=_rows(cur)
    visible_rows=[row for row in rows if not active_only or should_show_in_selection_offer_review(row)]
    from features import candidate_store
    for row in visible_rows:
        for candidate_key in (row.get('canonical_candidate_id'),row.get('candidate_id')):
            candidate=candidate_store.get_candidate(str(candidate_key)) if candidate_key else None
            if candidate and candidate.get('name'):
                row['candidate_name']=candidate['name']
                break
    return visible_rows


def event_detail(event_id:str,*,include_evidence:bool=False)->dict[str,Any]|None:
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT e.*,m.subject,m.sender_name,m.sender_email,m.recipient_email,
          m.sent_at AS email_sent_at,m.provider_message_id,m.provider_thread_id,
          m.body_text,m.html_body_text,m.mailbox_id,b.email_address AS mailbox_email
          FROM ai_recruitment_events e
          LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
          LEFT JOIN candidate_mailboxes b ON b.id=m.mailbox_id
          WHERE e.id=%s""",(event_id,));rows=_rows(cur)
    if not rows:return None
    row=rows[0]
    from services.recruitment_semantics import redact_sensitive_text
    structured=dict(row.get('structured_result') or {})
    structured['evidence']=[{**item,'text':redact_sensitive_text(str(item.get('text') or ''))} for item in structured.get('evidence') or [] if isinstance(item,dict)]
    row['structured_result']=structured
    row['email_body']=row.get('body_text') or row.get('html_body_text') or ''
    received=None
    if row.get('mailbox_id') and row.get('provider_thread_id'):
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute("""SELECT subject,sender_name,sender_email,recipient_email,sent_at,
              body_text,html_body_text
              FROM mailbox_messages
              WHERE mailbox_id=%s AND provider_thread_id=%s
                AND lower(COALESCE(sender_email,''))<>lower(COALESCE(%s,''))
                AND sent_at<=%s
              ORDER BY sent_at DESC LIMIT 1""",
              (row['mailbox_id'],row['provider_thread_id'],row.get('mailbox_email'),row.get('email_sent_at')))
            incoming=cur.fetchone()
            if incoming:
                received={
                    'subject':incoming[0],'sender_name':incoming[1],
                    'sender_email':incoming[2],'recipient_email':incoming[3],
                    'sent_at':incoming[4],
                    'body':incoming[5] or incoming[6] or '',
                }
    row['received_email']=received or {
        'subject':row.get('subject'),'sender_name':row.get('sender_name'),
        'sender_email':row.get('sender_email'),'recipient_email':row.get('recipient_email'),
        'sent_at':row.get('email_sent_at'),'body':row['email_body'],
    }
    row.pop('body_text',None);row.pop('html_body_text',None)
    row.pop('mailbox_email',None)
    # Extracted attachment text may contain bank/government identifiers. The
    # UI receives document metadata plus already-redacted evidence summaries.
    row['attachments']=attachments_for_message(row['mailbox_message_id'],include_text=False) if row.get('mailbox_message_id') else []
    return row


def event_reprocess_context(event_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT e.id AS event_id,m.*,b.email_address,
          b.candidate_id AS mailbox_candidate_id
          FROM ai_recruitment_events e JOIN mailbox_messages m ON m.id=e.mailbox_message_id
          JOIN candidate_mailboxes b ON b.id=m.mailbox_id WHERE e.id=%s""",(event_id,))
        rows=_rows(cur)
    if not rows:return None
    row=rows[0]
    row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['id'],include_text=True)]
    return row


def edit_event(event_id:str,changes:dict[str,Any],*,reviewer:str,notes:str='')->dict[str,Any]:
    allowed={'primary_status','classification','candidate_status','confidence','company_name','company_domain','job_title','recruiter_name','recruiter_email','interview_date','interview_time','interview_mode','offered_ctc','currency','joining_date','offer_date','offer_expiry_date','summary','requires_manual_review'}
    clean={k:v for k,v in changes.items() if k in allowed}
    if 'primary_status' in clean:
        from services.recruitment_mail_agent import STATUSES
        if clean['primary_status'] not in STATUSES:raise ValueError('Invalid recruitment status')
    if 'confidence' in clean and not 0<=float(clean['confidence'])<=1:raise ValueError('Confidence must be between 0 and 1')
    if 'classification' in clean and clean['classification'] not in CANONICAL_CLASSIFICATIONS:raise ValueError('Unsupported classification')
    if not clean:return event_detail(event_id) or {}
    before=event_detail(event_id) or {};assignments=', '.join(f'{k}=%s' for k in clean)
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"UPDATE ai_recruitment_events SET {assignments},corrected_result=%s::jsonb,review_notes=%s,updated_at=now() WHERE id=%s RETURNING *",(*clean.values(),json.dumps(clean,default=str),notes,event_id));rows=_rows(cur)
        if rows:cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at) VALUES(%s,%s,'admin','EVENT_EDITED',%s,%s,%s::jsonb,%s::jsonb,now())",(_id(),reviewer,rows[0]['candidate_id'],event_id,json.dumps({k:before.get(k) for k in clean},default=str),json.dumps(clean,default=str)))
    return rows[0] if rows else {}


def mailbox_stats(mailbox_id:str)->dict[str,Any]:
    with get_connection() as conn,conn.cursor() as cur:
        predicate,params=qualified_event_sql('e')
        cur.execute(f"""SELECT count(*) FILTER(WHERE {predicate}) important_emails,
          count(*) FILTER(WHERE e.primary_status IN('SELECTED','FINAL_SELECTION_CONFIRMED') AND {predicate}) selection_events,
          count(*) FILTER(WHERE e.primary_status IN('OFFER_INDICATION','OFFER_IN_PROGRESS','OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED','OFFER_ACCEPTED') AND {predicate}) offer_events,
          count(*) FILTER(WHERE e.primary_status='OFFER_LETTER_RECEIVED' AND {predicate}) offer_letters,
          0::bigint pending_reviews
          FROM mailbox_messages m LEFT JOIN ai_recruitment_events e ON e.mailbox_message_id=m.id WHERE m.mailbox_id=%s""",params*4+[mailbox_id]);names=[d.name for d in cur.description];stats=dict(zip(names,cur.fetchone()))
        # The mailbox card and Mail Alerts must use the same review queue.
        # Counting legacy PENDING events here made fully validated and
        # historical emails appear as work even though no alert existed.
        cur.execute("""SELECT count(*) FROM mail_monitoring_notifications
          WHERE gmail_account_id=%s
            AND priority='review_required'
            AND NOT is_reviewed
            AND dismissed_at IS NULL
            AND COALESCE(booking_status,'') <> 'Historical Skipped'""",(mailbox_id,))
        stats['pending_reviews']=int(cur.fetchone()[0])
        cur.execute("""SELECT id,status,job_type,created_at,started_at,completed_at,
          messages_fetched,messages_processed,events_detected,error_message
          FROM mailbox_sync_jobs WHERE mailbox_id=%s ORDER BY created_at DESC LIMIT 1""",(mailbox_id,))
        job_rows=_rows(cur)
        if job_rows:
            job=job_rows[0]
            stats.update({
                'latest_sync_job_id':job.get('id'),
                'latest_sync_status':job.get('status'),
                'latest_sync_job_type':job.get('job_type') or 'INCREMENTAL_SYNC',
                'latest_sync_created_at':job.get('created_at'),
                'latest_sync_started_at':job.get('started_at'),
                'latest_sync_completed_at':job.get('completed_at'),
                'latest_sync_messages_fetched':job.get('messages_fetched') or 0,
                'latest_sync_messages_processed':job.get('messages_processed') or 0,
                'latest_sync_events_detected':job.get('events_detected') or 0,
                'latest_sync_error':job.get('error_message'),
            })
        return stats


def summarize_selection_tracking_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure summary used by the API and regression tests."""
    lifecycle_groups = {
        'selected': ('SELECTED','FINAL_SELECTION_CONFIRMED'),
        'offers_received': ('OFFER_INDICATION','OFFER_IN_PROGRESS','OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED'),
        'offers_accepted': ('OFFER_ACCEPTED',),
        'joining_confirmed': ('JOINING_CONFIRMED',),
        'joined': ('JOINED',),
    }
    truth=[event for event in events if str(event.get('validation_status') or '').upper() in {'AUTO_VALIDATED','APPROVED'} and str(event.get('review_status') or '').upper() not in {'FALSE_POSITIVE','DUPLICATE','REJECTED','IGNORED'}]
    filters: dict[str,list[str]]={}
    for key,statuses in lifecycle_groups.items():
        filters[key]=sorted({str(event.get('canonical_candidate_id') or event.get('candidate_id')) for event in truth if event.get('primary_status') in statuses})
    filters['needs_review']=sorted({
        str(event.get('id')) for event in events
        if event.get('id')
        and str(event.get('review_status') or '').upper() == 'PENDING'
        and (
            str(event.get('validation_status') or '').upper() in {'NEEDS_REVIEW','RETRY_PENDING'}
            or str(event.get('cleanup_version') or '') == 'manual_content_audit_keep_v1'
        )
    })
    metrics={key:len(value) for key,value in filters.items()}
    # Backward-compatible aliases for older clients; all originate here.
    metrics.update({
        'pending_reviews':metrics['needs_review'],
        'selections_detected':metrics['selected'],
        'offers_accepted':metrics['offers_accepted'],
        'joining_confirmations':metrics['joining_confirmed'],
        'candidates_joined':metrics['joined'],
    })
    return {'metrics':metrics,'filters':filters}


def selection_tracking_stats() -> dict[str, Any]:
    """Central source of truth for all Selection & Offer dashboard cards."""
    predicate, params = qualified_event_sql('e')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT e.id,e.candidate_id,e.canonical_candidate_id,e.primary_status,
          e.review_status,e.validation_status,e.cleanup_version
          FROM ai_recruitment_events e WHERE {predicate}""",params)
        events=_rows(cur)
    return summarize_selection_tracking_events(events)


def review_event(event_id:str, action:str, reviewer:str, notes:str='', changes:dict[str,Any]|None=None)->dict[str,Any]:
    status={'approve':'APPROVED','reject':'REJECTED','false-positive':'FALSE_POSITIVE','duplicate':'DUPLICATE'}.get(action,action.upper())
    validation={'APPROVED':'APPROVED','FALSE_POSITIVE':'FALSE_POSITIVE','DUPLICATE':'REJECTED','REJECTED':'REJECTED'}.get(status,'NEEDS_REVIEW')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE ai_recruitment_events SET review_status=%s,validation_status=%s,reviewed_by=%s,reviewed_at=now(),review_notes=%s,updated_at=now() WHERE id=%s RETURNING *",(status,validation,reviewer,notes,event_id)); rows=_rows(cur)
        if rows:
            cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at) VALUES(%s,%s,'admin',%s,%s,%s,%s::jsonb,now())",(_id(),reviewer,'EVENT_'+status,rows[0]['candidate_id'],event_id,json.dumps({'notes':notes,'changes':changes or {}})))
    row=rows[0] if rows else {}
    if row and status=='APPROVED':
        classification=canonical_classification(row.get('structured_result') or {},row.get('primary_status'))
        candidate_status=str(row.get('candidate_status') or _CLASSIFICATION_STATUS[classification])
        apply_candidate_job_status(row,classification,candidate_status,force=True,updated_by=reviewer,review_notes=notes)
    elif row and status in {'REJECTED','FALSE_POSITIVE','DUPLICATE'}:
        rebuild_candidate_job_status(str(row.get('canonical_candidate_id') or row.get('candidate_id')))
    return row


def list_offer_cases(*, status:str|None=None, limit:int=50, offset:int=0)->list[dict[str,Any]]:
    predicate,predicate_params=qualified_event_sql('e')
    where=('c.verification_status=%s AND ' if status else "c.verification_status<>'IGNORED' AND ")+predicate
    params=([status] if status else [])+predicate_params+[limit,offset]
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"SELECT c.* FROM offer_verification_cases c JOIN ai_recruitment_events e ON e.id=c.ai_recruitment_event_id WHERE {where} ORDER BY c.created_at DESC LIMIT %s OFFSET %s",params);return _rows(cur)


def review_offer(case_id:str, action:str, reviewer:str, notes:str='')->dict[str,Any]:
    status={'verify':'VERIFIED','reject':'REJECTED','duplicate':'DUPLICATE','dispute':'CANDIDATE_DISPUTED'}.get(action)
    if not status:raise ValueError('Invalid offer review action')
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("UPDATE offer_verification_cases SET verification_status=%s,reviewed_by=%s,reviewed_at=now(),notes=%s,updated_at=now() WHERE id=%s RETURNING *",(status,reviewer,notes,case_id));rows=_rows(cur)
        if rows:cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at) VALUES(%s,%s,'admin',%s,%s,%s,%s::jsonb,now())",(_id(),reviewer,'OFFER_'+status,rows[0]['candidate_id'],case_id,json.dumps({'notes':notes})))
    return rows[0] if rows else {}


def canonical_classification(result: dict[str, Any] | None = None, status: str | None = None) -> str:
    result = result or {}
    explicit = str(result.get("classification") or "").strip().lower()
    if explicit in CANONICAL_CLASSIFICATIONS:
        return explicit
    return _STATUS_CLASSIFICATION.get(str(status or result.get("primary_status") or result.get("status") or "").upper(), "needs_review")


def notification_priority(classification: str, *, confidence: float, requires_review: bool = False) -> str:
    if requires_review or confidence < float(__import__('os').getenv('OLLAMA_CONFIDENCE_THRESHOLD', '0.75')):
        return "review_required"
    if classification in {"job_selection_confirmed", "offer_received", "joining_confirmed", "offer_accepted", "onboarding_started", "interview_confirmed", "interview_rescheduled", "interview_cancelled", "final_round_cleared"}:
        return "high"
    if classification in {"background_verification", "document_verification", "compensation_confirmation", "joining_date_updated", "hr_confirmation"}:
        return "medium"
    return "informational"


def record_analysis(
    message_id: str,
    candidate_id: str,
    result: dict[str, Any] | None,
    *,
    model: str | None,
    processing_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    value = dict(result or {})
    classification = canonical_classification(value)
    confidence = max(0.0, min(1.0, float(value.get("confidence") or 0)))
    candidate_status = str(value.get("candidate_status") or _CLASSIFICATION_STATUS[classification])
    analysis_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mail_ai_analyses(
          id,mailbox_message_id,candidate_id,model_name,model_version,classification,candidate_status,
          confidence,summary,reason,recommended_action,raw_ai_response,validated_response,
          processing_status,error_code,error_message,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,now(),now())
          ON CONFLICT(mailbox_message_id) DO UPDATE SET model_name=EXCLUDED.model_name,
            model_version=EXCLUDED.model_version,classification=EXCLUDED.classification,
            candidate_status=EXCLUDED.candidate_status,confidence=EXCLUDED.confidence,
            summary=EXCLUDED.summary,reason=EXCLUDED.reason,recommended_action=EXCLUDED.recommended_action,
            raw_ai_response=EXCLUDED.raw_ai_response,validated_response=EXCLUDED.validated_response,
            processing_status=EXCLUDED.processing_status,error_code=EXCLUDED.error_code,
            error_message=EXCLUDED.error_message,updated_at=now() RETURNING *""",
          (analysis_id,message_id,candidate_id,model,(value.get('schema_version') or 'selection_offer_event_v1'),classification,
           candidate_status,confidence,str(value.get('summary') or '')[:1000],str(value.get('reason') or value.get('ignore_reason') or '')[:1000],
           str(value.get('recommended_action') or '')[:1000],json.dumps(value,default=str),json.dumps(value,default=str),
           processing_status,error_code,str(error_message or '')[:400] or None))
        row=_rows(cur)[0]
        cur.execute("""UPDATE mail_ai_analyses SET ai_status=%s,validation_status=%s,
          email_intent=%s,document_type=%s,evidence_summary=%s WHERE id=%s RETURNING *""",(
          value.get('ai_status') or ('RETRY_PENDING' if processing_status=='RETRY_PENDING' else 'ANALYZED'),
          value.get('validation_status') or 'NEEDS_REVIEW',value.get('email_intent'),value.get('document_type'),
          value.get('evidence_summary') or value.get('summary'),row['id']))
        return _rows(cur)[0]


def _candidate_snapshot(candidate_id: str, structured: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        from features import candidate_store
        row = candidate_store.get_candidate(candidate_id) or {}
    except Exception:
        row = {}
    candidate = structured.get("candidate") or {}
    return (row.get("name") or candidate.get("name"), candidate.get("email"))


def apply_candidate_job_status(event: dict[str, Any], classification: str, candidate_status: str, *, force: bool = False, updated_by: str = "system", review_notes: str = "") -> bool:
    """Apply only high-confidence, monotonic candidate status transitions."""
    confidence = float(event.get("confidence") or 0)
    threshold = max(0.0, min(1.0, float(__import__('os').getenv('OLLAMA_CONFIDENCE_THRESHOLD', __import__('os').getenv('AI_RECRUITMENT_AUTO_ACCEPT_THRESHOLD', '0.90')))))
    validation=str(event.get('validation_status') or (event.get('structured_result') or {}).get('validation_status') or '').upper()
    if not force and (validation != 'AUTO_VALIDATED' or confidence < threshold or classification in {"needs_review", "not_relevant", "interview_update"}):
        return False
    candidate_id = str(event.get("canonical_candidate_id") or canonical_candidate_id(str(event["candidate_id"])))
    new_rank = _STATUS_RANK.get(candidate_status, 0)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM candidate_job_status WHERE candidate_id=%s FOR UPDATE", (candidate_id,))
        previous_rows = _rows(cur) if cur.description else []
        previous = previous_rows[0] if previous_rows else None
        previous_status = previous.get("status") if previous else None
        previous_rank = int(previous.get("status_rank") or 0) if previous else -1
        valid_terminal = (
            classification in {"offer_declined","offer_revoked"} and previous_status in {"Offer Received","Offer Accepted"}
        ) or (classification == "candidate_rejected" and previous_rank < _STATUS_RANK["Selected"])
        if previous and new_rank < previous_rank and not valid_terminal and not force:
            return False
        cur.execute("SELECT provider_message_id FROM mailbox_messages WHERE id=%s", (event.get("mailbox_message_id"),))
        provider_row = cur.fetchone()
        gmail_message_id = provider_row[0] if provider_row else None
        cur.execute("""INSERT INTO candidate_job_status(candidate_id,status,status_rank,source,source_id,gmail_message_id,classification,confidence,validation_status,updated_at)
          VALUES(%s,%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,now())
          ON CONFLICT(candidate_id) DO UPDATE SET status=EXCLUDED.status,status_rank=EXCLUDED.status_rank,
            source=EXCLUDED.source,source_id=EXCLUDED.source_id,gmail_message_id=EXCLUDED.gmail_message_id,
          classification=EXCLUDED.classification,confidence=EXCLUDED.confidence,
          validation_status=EXCLUDED.validation_status,updated_at=now()""",
          (candidate_id,candidate_status,new_rank,event.get('id'),gmail_message_id,classification,confidence,'APPROVED' if force else validation))
        cur.execute("""INSERT INTO candidate_status_history(id,candidate_id,previous_detected_status,new_detected_status,
          confirmed_status,source_type,source_id,gmail_message_id,ai_classification,confidence,updated_by,
          reviewed_by,reviewed_at,review_notes,created_at)
          VALUES(%s,%s,%s,%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END,%s,now())""",
          (_id(),candidate_id,previous_status,candidate_status,candidate_status if force else None,event.get('id'),gmail_message_id,
           classification,confidence,updated_by,updated_by if force else None,force,review_notes if force else None))
        cur.execute("UPDATE candidate_status_history SET validation_status=%s WHERE source_id=%s AND validation_status IS NULL",('APPROVED' if force else validation,event.get('id')))
    return previous_status != candidate_status


def rebuild_candidate_job_status(candidate_id: str) -> None:
    """Rebuild one derived current-state row from validated event truth."""
    canonical = canonical_candidate_id(candidate_id)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT e.*,m.provider_message_id FROM ai_recruitment_events e
          LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
          WHERE COALESCE(e.canonical_candidate_id,e.candidate_id)=%s
            AND e.validation_status IN('AUTO_VALIDATED','APPROVED')
            AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE','REJECTED','IGNORED')
          ORDER BY CASE e.primary_status
            WHEN 'JOINED' THEN 90 WHEN 'POST_SELECTION_ONBOARDING' THEN 80
            WHEN 'JOINING_CONFIRMED' THEN 70 WHEN 'OFFER_ACCEPTED' THEN 60
            WHEN 'APPOINTMENT_LETTER_RECEIVED' THEN 50 WHEN 'OFFER_LETTER_RECEIVED' THEN 50
            WHEN 'OFFER_APPROVED' THEN 50 WHEN 'OFFER_IN_PROGRESS' THEN 50
            WHEN 'OFFER_INDICATION' THEN 50 WHEN 'FINAL_SELECTION_CONFIRMED' THEN 40
            WHEN 'SELECTED' THEN 40 ELSE 0 END DESC,e.created_at DESC LIMIT 1""",(canonical,))
        rows=_rows(cur)
        if not rows:
            cur.execute("DELETE FROM candidate_job_status WHERE candidate_id=%s AND source='AI Mail Monitoring'",(canonical,))
            return
        event=rows[0]
        classification=canonical_classification(event.get('structured_result') or {},event.get('primary_status'))
        status=str(event.get('candidate_status') or _CLASSIFICATION_STATUS[classification])
        rank=_STATUS_RANK.get(status,0)
        cur.execute("""INSERT INTO candidate_job_status(candidate_id,status,status_rank,source,source_id,gmail_message_id,
          classification,confidence,validation_status,updated_at)
          VALUES(%s,%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,now())
          ON CONFLICT(candidate_id) DO UPDATE SET status=EXCLUDED.status,status_rank=EXCLUDED.status_rank,
          source=EXCLUDED.source,source_id=EXCLUDED.source_id,gmail_message_id=EXCLUDED.gmail_message_id,
          classification=EXCLUDED.classification,confidence=EXCLUDED.confidence,
          validation_status=EXCLUDED.validation_status,updated_at=now()""",
          (canonical,status,rank,event.get('id'),event.get('provider_message_id'),classification,event.get('confidence'),event.get('validation_status')))


def notification_is_tracked(classification: str) -> bool:
    """Return True if the classification belongs to a tracked notification category.

    Mail Monitoring Notifications track only:
    - Auto interview slot booking (interview_confirmed, interview_rescheduled, interview_cancelled)
    - Job confirmed monitoring mails (job_selection_confirmed, offer_received, offer_accepted, joining_confirmed)
    """
    return classification in TRACKED_NOTIFICATION_CLASSIFICATIONS


def should_route_to_mail_alert(
    event: dict[str, Any],
    analysis: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
    today: date | None = None,
) -> bool:
    """Return whether a persisted event belongs on the Mail Alerts screen.

    Pending-review infrastructure failures are not useful by themselves.  The
    source still needs strong employment evidence, and interview alerts must
    refer to a current or future schedule.  This keeps historical interviews
    and the old Ollama-timeout false positives out of the administrator queue.
    """
    structured = event.get("structured_result") or {}
    if isinstance(structured, str):
        structured = json.loads(structured)
    if structured.get("_suppress_monitoring_notification"):
        return False

    classification = canonical_classification(
        analysis,
        str(event.get("primary_status") or ""),
    )
    if not notification_is_tracked(classification):
        return False

    evidence = structured.get("evidence") or []
    meanings = {
        str(item.get("meaning") or "").strip().upper()
        for item in evidence
        if isinstance(item, dict)
    }
    expected_meanings = {
        key
        for key, value in _STATUS_CLASSIFICATION.items()
        if value == classification
    }
    expected_meanings.add(classification.upper())

    requires_review = bool(
        event.get("requires_manual_review")
        or structured.get("requires_manual_review")
        or str(event.get("validation_status") or "").upper()
        in {"NEEDS_REVIEW", "RETRY_PENDING"}
    )
    if requires_review and not (
        meanings.intersection(expected_meanings)
        or meanings.intersection(IMPORTANT_ALERT_EVIDENCE_MEANINGS)
    ):
        return False

    if classification.startswith("interview_"):
        interview = structured.get("interview") or {}
        raw_date = str(interview.get("date") or event.get("interview_date") or "").strip()
        try:
            scheduled_date = date.fromisoformat(raw_date)
        except ValueError:
            return classification == "interview_cancelled"
        if scheduled_date < (today or date.today()):
            return False

        # A trusted calendar event is authoritative.  For fallback-only rows,
        # also demand an explicit interview subject so newsletter dates/links
        # cannot become interview alerts during an AI outage.
        source_name = str(structured.get("classification_source") or "").upper()
        if requires_review and source_name not in {
            "ICALENDAR_VERIFIED",
            "CALENDAR_VERIFIED",
        }:
            subject = str((source or {}).get("subject") or event.get("subject") or "")
            subject_text = subject.casefold()
            if not any(
                cue in subject_text
                for cue in ("interview", "technical screening", "screening round")
            ):
                return False

    return True


def create_monitoring_notification(event: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Create a user-facing notification only for tracked classifications.

    Only auto interview slot booking and job confirmed monitoring mails produce
    notifications. Other classifications are still processed for candidate status
    updates and offer tracking but do not generate notifications.
    """
    message_id = event.get("mailbox_message_id")
    structured = event.get("structured_result") or {}
    if isinstance(structured, str):
        structured = json.loads(structured)
    classification = str(analysis["classification"])
    candidate_status = str(analysis.get("candidate_status") or _CLASSIFICATION_STATUS[classification])

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m.provider_message_id,m.provider_thread_id,m.subject,m.sender_name,m.sender_email,m.sent_at,m.mailbox_id,m.recipient_email
          FROM mailbox_messages m WHERE m.id=%s""", (message_id,))
        source = cur.fetchone()
        if not source:
            raise ValueError("Mailbox message not found for notification")
        provider_id,thread_id,subject,sender_name,sender_email,sent_at,mailbox_id,recipient_email = source
        source_row = {
            "provider_message_id": provider_id,
            "provider_thread_id": thread_id,
            "subject": subject,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "sent_at": sent_at,
            "mailbox_id": mailbox_id,
            "recipient_email": recipient_email,
        }
        if not should_route_to_mail_alert(event, analysis, source=source_row):
            return {}

        name, email = _candidate_snapshot(str(event["candidate_id"]), structured)
        notification_id = _id()
        company = structured.get("company") or {}
        job = structured.get("job") or {}
        confidence = float(event.get("confidence") or analysis.get("confidence") or 0)
        priority = notification_priority(classification, confidence=confidence, requires_review=bool(event.get("requires_manual_review")))
        reason = str(structured.get("reason") or structured.get("ignore_reason") or '')[:1000]
        action = str(structured.get("recommended_action") or '')[:1000]
        cur.execute("""INSERT INTO mail_monitoring_notifications(id,candidate_id,candidate_name,candidate_email,
          gmail_account_id,gmail_message_id,gmail_thread_id,email_analysis_id,ai_recruitment_event_id,
          classification,candidate_status,company_name,job_role,email_subject,sender_name,sender_email,
          email_received_at,ai_confidence,ai_summary,ai_reason,recommended_action,priority,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
          ON CONFLICT(gmail_message_id,classification) DO UPDATE SET updated_at=now() RETURNING *""",
          (notification_id,event['candidate_id'],name,email or recipient_email,mailbox_id,provider_id,thread_id,analysis['id'],event['id'],
           classification,candidate_status,company.get('name') or event.get('company_name'),job.get('title') or event.get('job_title'),
           subject,sender_name,sender_email,sent_at,confidence,str(event.get('summary') or structured.get('summary') or '')[:1000],
           reason,action,priority))
        return _rows(cur)[0]


def finalize_detection(event: dict[str, Any], *, result: dict[str, Any], model: str, duration_ms: int) -> dict[str, Any]:
    """Persist analysis, safe candidate state and notification after the event."""
    classification = canonical_classification(result)
    candidate_status = str(result.get("candidate_status") or _CLASSIFICATION_STATUS[classification])
    result["classification"] = classification
    result["candidate_status"] = candidate_status
    try:
        from features import candidate_store
        mapping_confirmed = candidate_store.get_candidate(str(event["candidate_id"])) is not None
    except Exception:
        mapping_confirmed = False
    if not mapping_confirmed:
        classification="needs_review";candidate_status="Needs Review"
        result.update(classification=classification,candidate_status=candidate_status,requires_manual_review=True,
                      reason="Candidate mapping could not be confirmed",risk_flags=list(dict.fromkeys((result.get('risk_flags') or [])+['CANDIDATE_MAPPING_ISSUE'])))
    validation_status=str(result.get('validation_status') or event.get('validation_status') or 'NEEDS_REVIEW').upper()
    processing_status='RETRY_PENDING' if validation_status=='RETRY_PENDING' else 'CLASSIFIED'
    analysis = record_analysis(event["mailbox_message_id"], event["candidate_id"], result, model=model, processing_status=processing_status)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE ai_recruitment_events SET classification=%s,candidate_status=%s,ai_reason=%s,
          recommended_action=%s,original_ai_result=COALESCE(original_ai_result,%s::jsonb),updated_at=now()
          WHERE id=%s RETURNING *""", (classification,candidate_status,str(result.get('reason') or result.get('ignore_reason') or '')[:1000],
          str(result.get('recommended_action') or '')[:1000],json.dumps(result,default=str),event['id']))
        event = _rows(cur)[0]
    event['validation_status']=validation_status
    status_updated = apply_candidate_job_status(event, classification, candidate_status)
    if not status_updated and classification not in {"needs_review","not_relevant","interview_update"}:
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute("SELECT status,status_rank FROM candidate_job_status WHERE candidate_id=%s",(event['candidate_id'],));current=cur.fetchone()
        if current and current[0] != candidate_status and int(current[1] or 0) > _STATUS_RANK.get(candidate_status,0):
            event['requires_manual_review']=True
            event['status_conflict']=True
    notification = create_monitoring_notification(event, analysis)
    event["classification"] = classification
    event["candidate_status"] = candidate_status
    event["notification"] = notification
    event["candidate_status_updated"] = status_updated
    return event


def record_realtime_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event_id = _id()
    safe = dict(payload)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mail_realtime_events(id,event_type,notification_id,candidate_id,payload,created_at)
          VALUES(%s,%s,%s,%s,%s::jsonb,now()) RETURNING *""",
          (event_id,event_type,safe.get('notification_id'),safe.get('candidate_id'),json.dumps(safe,default=str)))
        return _rows(cur)[0]


def list_realtime_events(*, after_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if after_id:
        where = "WHERE created_at>(SELECT created_at FROM mail_realtime_events WHERE id=%s)"
        params.append(after_id)
    params.append(max(1, min(limit, 500)))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM mail_realtime_events {where} ORDER BY created_at ASC LIMIT %s", params)
        return _rows(cur)


def list_notifications(*, filters: dict[str, Any] | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    filters = filters or {}
    where: list[str] = [
        "dismissed_at IS NULL",
        "COALESCE(booking_status,'') <> 'Historical Skipped'",
    ]
    params: list[Any] = []
    exact = {"candidate_id", "candidate_status", "priority"}
    # When no specific classification filter is provided, default to tracked
    # notification classifications (auto interview booking + job confirmed).
    # This ensures non-tracked classifications are excluded by default.
    if filters.get("classification"):
        where.append("classification=%s")
        params.append(filters["classification"])
    else:
        placeholders = ", ".join("%s" for _ in TRACKED_NOTIFICATION_CLASSIFICATIONS)
        where.append(f"classification IN ({placeholders})")
        params.extend(TRACKED_NOTIFICATION_CLASSIFICATIONS)
    for field in ("is_read", "is_reviewed"):
        if filters.get(field) is not None:
            where.append(f"{field}=%s")
            params.append(bool(filters[field]))
    if filters.get("company"):
        where.append("company_name ILIKE %s"); params.append(f"%{filters['company']}%")
    if filters.get("search"):
        where.append("concat_ws(' ',candidate_name,candidate_email,company_name,job_role,email_subject,sender_email,ai_summary) ILIKE %s")
        params.append(f"%{filters['search']}%")
    if filters.get("confidence_min") is not None:
        where.append("ai_confidence>=%s"); params.append(float(filters['confidence_min']))
    if filters.get("confidence_max") is not None:
        where.append("ai_confidence<=%s"); params.append(float(filters['confidence_max']))
    if filters.get("date_from"):
        where.append("created_at::date>=%s"); params.append(filters['date_from'])
    if filters.get("date_to"):
        where.append("created_at::date<=%s"); params.append(filters['date_to'])
    clause = " AND ".join(where)
    order = "ASC" if str(filters.get("sort") or "").lower() == "oldest" else "DESC"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM mail_monitoring_notifications WHERE {clause}", params)
        total = int(cur.fetchone()[0])
        cur.execute(f"SELECT * FROM mail_monitoring_notifications WHERE {clause} ORDER BY created_at {order} LIMIT %s OFFSET %s", params + [max(1,min(limit,100)),max(0,offset)])
        rows = _rows(cur)
    return rows, total


def notification_summary() -> dict[str, Any]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) FILTER(WHERE dismissed_at IS NULL) visible_total,
          count(*) FILTER(WHERE NOT is_read AND dismissed_at IS NULL) unread,
          count(*) FILTER(WHERE classification='offer_received' AND dismissed_at IS NULL) new_offers,
          count(*) FILTER(WHERE classification='job_selection_confirmed' AND dismissed_at IS NULL) selections,
          count(*) FILTER(WHERE classification='joining_confirmed' AND dismissed_at IS NULL) joining_confirmations,
          count(*) FILTER(WHERE classification='interview_confirmed' AND booking_status='Auto Booked' AND dismissed_at IS NULL) auto_booked_interviews,
          count(*) FILTER(WHERE booking_status IN('Blocked','Processing Failed') AND dismissed_at IS NULL) booking_blocked,
          count(*) FILTER(WHERE priority='review_required' AND NOT is_reviewed AND dismissed_at IS NULL) needs_review,
          count(*) FILTER(WHERE classification IN ('offer_received','offer_accepted','job_selection_confirmed') AND dismissed_at IS NULL) job_confirmed_count,
          count(*) FILTER(WHERE classification IN ('interview_confirmed','interview_rescheduled','interview_cancelled') AND dismissed_at IS NULL) interview_booking_count
          FROM mail_monitoring_notifications
          WHERE COALESCE(booking_status,'') <> 'Historical Skipped'""")
        names = [d.name for d in cur.description]
        return dict(zip(names, cur.fetchone()))


def clear_notifications(*, reviewer: str) -> int:
    """Dismiss every currently visible notification without deleting evidence."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mail_monitoring_notifications SET
          dismissed_at=now(),is_read=true,read_at=COALESCE(read_at,now()),updated_at=now()
          WHERE dismissed_at IS NULL RETURNING id""")
        cleared = len(cur.fetchall())
    return cleared


def update_notification(notification_id: str, action: str, *, reviewer: str, notes: str = "", changes: dict[str, Any] | None = None) -> dict[str, Any]:
    changes = dict(changes or {})
    corrected_event: dict[str, Any] | None = None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mail_monitoring_notifications WHERE id=%s FOR UPDATE", (notification_id,))
        rows = _rows(cur)
        if not rows:
            return {}
        before = rows[0]
        assignments: dict[str, Any] = {}
        if action == "read": assignments.update(is_read=True, read_at=now())
        elif action == "unread": assignments.update(is_read=False, read_at=None)
        elif action == "reviewed": assignments.update(is_reviewed=True, reviewed_at=now(), reviewed_by=reviewer, review_notes=notes)
        elif action == "dismiss": assignments.update(dismissed_at=now())
        elif action == "false-detection": assignments.update(is_false_detection=True,is_reviewed=True,reviewed_at=now(),reviewed_by=reviewer,review_notes=notes)
        elif action == "correct":
            classification = str(changes.get('classification') or '').lower()
            if classification not in CANONICAL_CLASSIFICATIONS: raise ValueError('Unsupported classification')
            assignments.update(classification=classification,candidate_status=str(changes.get('candidate_status') or _CLASSIFICATION_STATUS[classification]),is_reviewed=True,reviewed_at=now(),reviewed_by=reviewer,review_notes=notes)
        else: raise ValueError('Unsupported notification action')
        sql = ",".join(f"{key}=%s" for key in assignments)
        cur.execute(f"UPDATE mail_monitoring_notifications SET {sql},updated_at=now() WHERE id=%s RETURNING *", (*assignments.values(),notification_id))
        updated = _rows(cur)[0]
        if action in {'reviewed','false-detection','correct'}:
            cur.execute("""INSERT INTO mail_review_evaluations(id,notification_id,email_analysis_id,original_classification,
              corrected_classification,original_candidate_status,corrected_candidate_status,original_confidence,
              is_false_detection,review_notes,reviewed_by,created_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())""",
              (_id(),notification_id,before.get('email_analysis_id'),before.get('classification'),updated.get('classification'),
               before.get('candidate_status'),updated.get('candidate_status'),before.get('ai_confidence'),action=='false-detection',notes,reviewer))
        if action=='correct' and before.get('ai_recruitment_event_id'):
            correction={'classification':updated.get('classification'),'candidate_status':updated.get('candidate_status'),'review_notes':notes,'reviewed_by':reviewer}
            cur.execute("""UPDATE ai_recruitment_events SET classification=%s,candidate_status=%s,
              corrected_result=%s::jsonb,review_status='APPROVED',reviewed_by=%s,reviewed_at=now(),review_notes=%s,updated_at=now()
              WHERE id=%s RETURNING *""",(updated.get('classification'),updated.get('candidate_status'),json.dumps(correction),reviewer,notes,before.get('ai_recruitment_event_id')))
            event_rows=_rows(cur)
            corrected_event=event_rows[0] if event_rows else None
    if corrected_event:
        apply_candidate_job_status(corrected_event,str(updated['classification']),str(updated['candidate_status']),force=True,updated_by=reviewer,review_notes=notes)
    return updated


def notification_reprocess_context(notification_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT n.*,m.id AS mailbox_message_id,m.provider_message_id,m.provider_thread_id,
          m.sender_name,m.sender_email,m.recipient_email,m.subject,m.sent_at,m.body_text,m.html_body_text,
          b.* FROM mail_monitoring_notifications n
          JOIN mailbox_messages m ON m.provider_message_id=n.gmail_message_id AND m.mailbox_id=n.gmail_account_id
          JOIN candidate_mailboxes b ON b.id=m.mailbox_id WHERE n.id=%s""",(notification_id,))
        rows=_rows(cur)
    if not rows:return None
    row=rows[0]
    row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['mailbox_message_id'],include_text=True)]
    return row


def record_interview_analysis(
    *, mailbox_message_id: str, email_analysis_id: str | None,
    mailbox_id: str, gmail_message_id: str, gmail_thread_id: str | None,
    candidate_id: str, result: dict[str, Any], validation_status: str,
    processing_status: str,
) -> dict[str, Any]:
    interview = dict(result.get("interview") or {})
    classification = canonical_classification(result)
    analysis_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO interview_mail_analyses(id,mailbox_message_id,email_analysis_id,
              gmail_account_id,gmail_message_id,gmail_thread_id,candidate_id,classification,
              is_interview_email,company_name,job_role,interview_round,interview_date,
              interview_time,timezone,meeting_link,interview_mode,location,ai_confidence,
              ai_summary,ai_reason,validation_status,processing_status,structured_result,
              created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now(),now())
              ON CONFLICT(mailbox_message_id) DO UPDATE SET
                email_analysis_id=EXCLUDED.email_analysis_id,classification=EXCLUDED.classification,
                is_interview_email=EXCLUDED.is_interview_email,company_name=EXCLUDED.company_name,
                job_role=EXCLUDED.job_role,interview_round=EXCLUDED.interview_round,
                interview_date=EXCLUDED.interview_date,interview_time=EXCLUDED.interview_time,
                timezone=EXCLUDED.timezone,meeting_link=EXCLUDED.meeting_link,
                interview_mode=EXCLUDED.interview_mode,location=EXCLUDED.location,
                ai_confidence=EXCLUDED.ai_confidence,ai_summary=EXCLUDED.ai_summary,
                ai_reason=EXCLUDED.ai_reason,validation_status=EXCLUDED.validation_status,
                processing_status=EXCLUDED.processing_status,structured_result=EXCLUDED.structured_result,
                updated_at=now() RETURNING *""",
            (
                analysis_id, mailbox_message_id, email_analysis_id, mailbox_id, gmail_message_id,
                gmail_thread_id, candidate_id, classification,
                classification.startswith("interview_"),
                (result.get("company") or {}).get("name"), (result.get("job") or {}).get("title"),
                interview.get("round"), interview.get("date") or None, interview.get("time"),
                interview.get("timezone"), interview.get("meeting_link"), interview.get("mode"),
                interview.get("location"), float(result.get("confidence") or 0),
                result.get("summary"), result.get("reason"), validation_status,
                processing_status, json.dumps(result, default=str),
            ),
        )
        return _rows(cur)[0]


def record_booking_audit(
    *, analysis_id: str | None, candidate_id: str, gmail_message_id: str,
    gmail_thread_id: str | None, classification: str, booking_id: str | None,
    auto_booked: bool, validation_status: str, payment_status: str,
    duplicate_status: str, conflict_status: str, booking_status: str,
    previous_booking: dict[str, Any] | None = None,
    new_booking: dict[str, Any] | None = None, failure_code: str | None = None,
    failure_message: str | None = None, correlation_id: str | None = None,
) -> dict[str, Any]:
    audit_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO interview_auto_booking_audit(id,booking_id,source,gmail_message_id,
              gmail_thread_id,email_analysis_id,candidate_id,classification,auto_booked,
              validation_status,payment_validation_status,duplicate_check_status,
              conflict_check_status,booking_status,previous_booking,new_booking,failure_code,
              failure_message,correlation_id,created_at,updated_at)
              VALUES(%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                     %s::jsonb,%s::jsonb,%s,%s,%s,now(),now())
              ON CONFLICT(gmail_message_id,classification) DO UPDATE SET
                booking_id=EXCLUDED.booking_id,email_analysis_id=EXCLUDED.email_analysis_id,
                auto_booked=EXCLUDED.auto_booked,validation_status=EXCLUDED.validation_status,
                payment_validation_status=EXCLUDED.payment_validation_status,
                duplicate_check_status=EXCLUDED.duplicate_check_status,
                conflict_check_status=EXCLUDED.conflict_check_status,
                booking_status=EXCLUDED.booking_status,previous_booking=EXCLUDED.previous_booking,
                new_booking=EXCLUDED.new_booking,failure_code=EXCLUDED.failure_code,
                failure_message=EXCLUDED.failure_message,correlation_id=EXCLUDED.correlation_id,
                updated_at=now()
              RETURNING *""",
            (audit_id, booking_id, gmail_message_id, gmail_thread_id, analysis_id,
             candidate_id, classification, auto_booked, validation_status, payment_status,
             duplicate_status, conflict_status, booking_status,
             json.dumps(previous_booking or {}, default=str), json.dumps(new_booking or {}, default=str),
             failure_code, str(failure_message or "")[:1000] or None, correlation_id),
        )
        return _rows(cur)[0]


def booking_audit_for_message(gmail_message_id: str, classification: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM interview_auto_booking_audit WHERE gmail_message_id=%s AND classification=%s LIMIT 1",
            (gmail_message_id, classification),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def notification_for_event(event_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM mail_monitoring_notifications WHERE ai_recruitment_event_id=%s ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def attach_booking_to_notification(
    notification_id: str, *, audit_id: str, booking_id: str | None,
    booking_status: str, result: dict[str, Any], priority: str | None = None,
    display_status: str | None = None, detail: str | None = None,
    schedule: dict[str, str] | None = None,
    block_reason: dict[str, str] | None = None,
) -> dict[str, Any]:
    interview = result.get("interview") or {}
    # A successful booking is always stored in the operational Asia/Kolkata
    # timezone. Notifications must display that same normalized schedule,
    # rather than the calendar provider's equivalent source-timezone value.
    display_date = (schedule or {}).get("date") or interview.get("date") or None
    display_time = (schedule or {}).get("time") or interview.get("time")
    display_timezone = "Asia/Kolkata" if schedule else interview.get("timezone")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_monitoring_notifications SET notification_type='interview_booking',
              booking_id=%s,booking_audit_id=%s,booking_status=%s,interview_round=%s,
              interview_date=%s,interview_time=%s,interview_timezone=%s,interview_mode=%s,
              meeting_link=%s,priority=COALESCE(%s,priority),
              candidate_status=COALESCE(%s,candidate_status),
              recommended_action=COALESCE(%s,recommended_action),
              booking_block_reason_code=%s,booking_block_reason=%s,
              booking_failure_code=%s,updated_at=now()
              WHERE id=%s RETURNING *""",
            (booking_id, audit_id, booking_status, interview.get("round"),
             display_date, display_time, display_timezone,
             interview.get("mode"), interview.get("meeting_link"), priority,
             display_status, detail,
             # Written unconditionally, not COALESCEd: a booking that later
             # succeeds must clear the reason it was previously blocked for.
             (block_reason or {}).get("reason_code"),
             (block_reason or {}).get("reason"),
             (block_reason or {}).get("internal_code"),
             notification_id),
        )
        rows = _rows(cur)
    return rows[0] if rows else {}


def list_booking_audit(*, candidate_id: str | None = None, booking_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if candidate_id:
        where.append("candidate_id=%s"); params.append(candidate_id)
    if booking_id:
        where.append("booking_id=%s"); params.append(booking_id)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM interview_auto_booking_audit{clause} ORDER BY created_at DESC LIMIT %s",
            params + [max(1, min(limit, 200))],
        )
        return _rows(cur)
