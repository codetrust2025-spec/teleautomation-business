"""Link one recovered interview email to one already-confirmed booking.

Reconciliation is evidence bookkeeping, not booking creation.  This module
therefore never calls the automatic-booking engine and never updates a
candidate booking.  It locks and hashes the booking only to prove that the
relationship write left the protected schedule and identity fields unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from typing import Any

from core.db.connection import get_connection
from core import recruitment_mail_store as mail_store
from features import candidate_identity, candidate_store


TERMINAL_MESSAGE_STATUS = "INTERVIEW_RECONCILED"
RECONCILIATION_STATUS = "EXISTING_BOOKING_LINKED"
RECONCILIATION_REASON = "EXISTING_BOOKING_RECONCILIATION"
RECONCILABLE_MESSAGE_STATUSES = frozenset({"AI_QUEUED", "AI_RETRY_PENDING"})

BOOKING_PROTECTED_FIELDS = (
    "id",
    "name",
    "date",
    "time",
    "time_end",
    "interview_round",
    "slot_confirmed",
    "interview_booking_source",
    "interview_company",
)


class InterviewReconciliationError(ValueError):
    """A failed invariant which must roll back the entire reconciliation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _dict_row(cur, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {column.name: value for column, value in zip(cur.description, row)}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normal_date(value: Any, *, field: str = "interview date") -> str:
    raw = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise InterviewReconciliationError(
            "INVALID_DATE", f"{field.capitalize()} must be YYYY-MM-DD."
        ) from exc


def _normal_time(value: Any, *, field: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip().lower().replace(".", ":"))
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I %p"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise InterviewReconciliationError(
        "INVALID_EXPECTED_TIME", f"Expected {field} must be an unambiguous time."
    )


def booking_protected_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable booking fields used by recovery preconditions."""
    return {field: payload.get(field) for field in BOOKING_PROTECTED_FIELDS}


def booking_protected_hash(payload: dict[str, Any]) -> str:
    """Hash protected booking fields using the established recovery format."""
    encoded = json.dumps(
        booking_protected_snapshot(payload), sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _analysis_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preliminary_candidate_id(mailbox_message_id: str, gmail_message_id: str) -> str:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT candidate_id FROM mailbox_messages
               WHERE id=%s AND provider_message_id=%s""",
            (mailbox_message_id, gmail_message_id),
        )
        rows = cur.fetchall()
    if not rows:
        raise InterviewReconciliationError("MESSAGE_NOT_FOUND", "Mailbox message was not found.")
    if len(rows) != 1:
        raise InterviewReconciliationError(
            "MESSAGE_NOT_UNIQUE", "Mailbox message identity is not unique."
        )
    return str(rows[0][0] or "")


def _lock_message(cur, mailbox_message_id: str, gmail_message_id: str) -> dict[str, Any]:
    cur.execute(
        """SELECT m.*,b.email_address
           FROM mailbox_messages m
           JOIN candidate_mailboxes b ON b.id=m.mailbox_id
           WHERE m.id=%s AND m.provider_message_id=%s
           FOR UPDATE OF m""",
        (mailbox_message_id, gmail_message_id),
    )
    rows = cur.fetchall()
    if not rows:
        raise InterviewReconciliationError("MESSAGE_NOT_FOUND", "Mailbox message was not found.")
    if len(rows) != 1:
        raise InterviewReconciliationError(
            "MESSAGE_NOT_UNIQUE", "Mailbox message identity is not unique."
        )
    return _dict_row(cur, rows[0]) or {}


def _lock_booking(cur, booking_id: str) -> dict[str, Any]:
    cur.execute(
        "SELECT payload FROM candidates_store WHERE id=%s FOR UPDATE", (booking_id,)
    )
    row = cur.fetchone()
    if not row:
        raise InterviewReconciliationError("BOOKING_NOT_FOUND", "Booking was not found.")
    payload = _json_object(row[0])
    if not payload:
        raise InterviewReconciliationError(
            "BOOKING_INVALID", "Stored booking payload is invalid."
        )
    return payload


def _canonical_identity(cur, candidate_id: str) -> str:
    """Label one candidate with the representative of its identity cluster.

    This used to be a single ``candidate_identity_links`` hop, which is not
    transitive and not idempotent — see ``features.candidate_identity`` for why
    that table alone cannot answer the question. The label is only ever written
    onto the evidence row; the safety decision below uses cluster membership.
    """
    try:
        cluster = candidate_identity.identity_cluster(cur, candidate_id)
    except candidate_identity.IdentityClusterTooLarge as exc:
        raise InterviewReconciliationError("IDENTITY_RESOLUTION_UNSAFE", str(exc)) from exc
    if not cluster:
        return str(candidate_id or "")
    return candidate_identity.cluster_representative(cur, cluster) or str(
        candidate_id or ""
    )


def _same_identity(cur, message_candidate_id: str, booking_candidate_id: str) -> bool:
    """Do the mail and the booking describe one person?

    Compares identity *clusters*, not single-hop canonical ids. Two rows of the
    same person routinely carry different canonical ids — migration 010 assigns
    them per key group rather than per person — so comparing labels rejects
    legitimate reconciliations and, worse, could accept two people who happen
    to share a stale label.
    """
    try:
        return candidate_identity.same_identity(
            cur, message_candidate_id, booking_candidate_id
        )
    except candidate_identity.IdentityClusterTooLarge as exc:
        # A closure this wide means a blank or shared key has joined unrelated
        # people. Refusing is the only safe answer: silently proceeding could
        # attach one person's interview evidence to another's booking.
        raise InterviewReconciliationError("IDENTITY_RESOLUTION_UNSAFE", str(exc)) from exc


def _failed_analysis(cur, mailbox_message_id: str) -> tuple[dict[str, Any], str]:
    cur.execute(
        "SELECT to_jsonb(a) FROM mail_ai_analyses a WHERE mailbox_message_id=%s FOR SHARE",
        (mailbox_message_id,),
    )
    row = cur.fetchone()
    if not row:
        raise InterviewReconciliationError(
            "FAILED_ANALYSIS_NOT_FOUND",
            "The failed mail analysis required for reconciliation was not found.",
        )
    analysis = _json_object(row[0])
    return analysis, _analysis_fingerprint(analysis)


def _relationship_for_message(cur, mailbox_message_id: str) -> dict[str, Any] | None:
    cur.execute(
        """SELECT * FROM interview_mail_analyses
           WHERE mailbox_message_id=%s FOR UPDATE""",
        (mailbox_message_id,),
    )
    return _dict_row(cur, cur.fetchone())


def _audit_exists(cur, relationship_id: str) -> bool:
    cur.execute(
        """SELECT 1 FROM recruitment_audit_log
           WHERE action='INTERVIEW_MESSAGE_RECONCILED' AND source_id=%s LIMIT 1""",
        (relationship_id,),
    )
    return cur.fetchone() is not None


def _upsert_relationship(
    cur,
    *,
    relationship_id: str,
    message: dict[str, Any],
    canonical_candidate_id: str,
    failed_analysis_id: str,
    booking_id: str,
    company_name: str,
    expected_date: str,
    expected_time: str,
    expected_time_end: str,
    expected_round: str,
    booking_hash: str,
    reconciled_by: str,
) -> dict[str, Any]:
    reconciliation = {
        "type": RECONCILIATION_STATUS,
        "source": "RECOVERED_SOURCE_INVITE",
        "booking_hash": booking_hash,
        "company": {"name": company_name},
        "interview": {
            "date": expected_date,
            "time": expected_time,
            "end_time": expected_time_end,
            "timezone": "Asia/Kolkata",
            "round": expected_round,
        },
    }
    structured = {"reconciliation": reconciliation}
    cur.execute(
        """INSERT INTO interview_mail_analyses(
             id,mailbox_message_id,email_analysis_id,gmail_account_id,gmail_message_id,
             gmail_thread_id,candidate_id,classification,is_interview_email,company_name,
             interview_round,interview_date,interview_time,timezone,ai_confidence,
             ai_summary,ai_reason,validation_status,processing_status,structured_result,
             reconciled_booking_id,reconciliation_status,reconciled_by,reconciled_at,
             created_at,updated_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,'interview_confirmed',true,%s,%s,%s,%s,
             'Asia/Kolkata',0,'Recovered interview invite reconciled to an existing booking.',
             'Existing confirmed booking linked without creating another booking.',
             'MANUAL_RECONCILED','RESOLVED',%s::jsonb,%s,%s,%s,now(),now(),now())
           ON CONFLICT(mailbox_message_id) DO UPDATE SET
             email_analysis_id=COALESCE(interview_mail_analyses.email_analysis_id,EXCLUDED.email_analysis_id),
             company_name=COALESCE(interview_mail_analyses.company_name,EXCLUDED.company_name),
             structured_result=COALESCE(interview_mail_analyses.structured_result,'{}'::jsonb)
               || jsonb_build_object('reconciliation',EXCLUDED.structured_result->'reconciliation'),
             reconciled_booking_id=EXCLUDED.reconciled_booking_id,
             reconciliation_status=EXCLUDED.reconciliation_status,
             reconciled_by=COALESCE(interview_mail_analyses.reconciled_by,EXCLUDED.reconciled_by),
             reconciled_at=COALESCE(interview_mail_analyses.reconciled_at,EXCLUDED.reconciled_at),
             updated_at=now()
           WHERE interview_mail_analyses.reconciled_booking_id IS NULL
              OR interview_mail_analyses.reconciled_booking_id=EXCLUDED.reconciled_booking_id
           RETURNING *""",
        (
            relationship_id,
            message["id"],
            failed_analysis_id,
            message["mailbox_id"],
            message["provider_message_id"],
            message.get("provider_thread_id"),
            canonical_candidate_id,
            company_name,
            expected_round,
            expected_date,
            expected_time,
            json.dumps(structured, default=str),
            booking_id,
            RECONCILIATION_STATUS,
            reconciled_by,
        ),
    )
    relationship = _dict_row(cur, cur.fetchone())
    if not relationship:
        raise InterviewReconciliationError(
            "RECONCILIATION_CONFLICT",
            "Message is already reconciled to a different booking.",
        )
    return relationship


def _terminalize_message(cur, message: dict[str, Any]) -> dict[str, Any]:
    cur.execute(
        """UPDATE mailbox_messages SET
             previous_processing_status=CASE
               WHEN processing_status<>%s THEN processing_status
               ELSE previous_processing_status END,
             processing_status=%s,ignore_reason=%s,
             ai_retry_after=NULL,ai_lease_expires_at=NULL,
             reprocessed_at=COALESCE(reprocessed_at,now()),
             reprocessing_reason=%s,updated_at=now()
           WHERE id=%s
           RETURNING id,processing_status,previous_processing_status,
                     ai_retry_after,ai_lease_expires_at""",
        (
            TERMINAL_MESSAGE_STATUS,
            TERMINAL_MESSAGE_STATUS,
            RECONCILIATION_STATUS,
            RECONCILIATION_REASON,
            message["id"],
        ),
    )
    return _dict_row(cur, cur.fetchone()) or {}


def _write_audit(
    cur,
    *,
    relationship_id: str,
    canonical_candidate_id: str,
    message: dict[str, Any],
    booking_id: str,
    booking_hash: str,
    company_name: str,
    reconciled_by: str,
) -> str:
    audit_id = str(uuid.uuid4())
    previous = {
        "message_status": message.get("processing_status"),
        "booking_hash": booking_hash,
    }
    new = {
        "message_status": TERMINAL_MESSAGE_STATUS,
        "reconciliation_status": RECONCILIATION_STATUS,
        "mailbox_message_id": message.get("id"),
        "gmail_message_id": message.get("provider_message_id"),
        "booking_id": booking_id,
        "booking_hash": booking_hash,
        "company": company_name,
    }
    cur.execute(
        """INSERT INTO recruitment_audit_log(
             id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
           VALUES(%s,%s,'admin','INTERVIEW_MESSAGE_RECONCILED',%s,%s,%s::jsonb,%s::jsonb,now())""",
        (
            audit_id,
            reconciled_by,
            canonical_candidate_id,
            relationship_id,
            json.dumps(previous, default=str),
            json.dumps(new, default=str),
        ),
    )
    return audit_id


def _validate_booking(
    booking: dict[str, Any],
    *,
    expected_candidate_name: str,
    expected_date: str,
    expected_time: str,
    expected_time_end: str,
    expected_round: str,
    expected_booking_hash: str,
) -> str:
    actual_name = candidate_store.canonical_candidate_name(str(booking.get("name") or ""))
    expected_name = candidate_store.canonical_candidate_name(expected_candidate_name)
    if not expected_name or actual_name.casefold() != expected_name.casefold():
        raise InterviewReconciliationError(
            "CANDIDATE_MISMATCH", "Booking candidate does not match expected evidence."
        )
    actual_date = _normal_date(booking.get("date"))
    actual_time = _normal_time(booking.get("time"), field="booking start time")
    actual_end = _normal_time(booking.get("time_end"), field="booking end time")
    actual_round = candidate_store.normalise_interview_round(booking.get("interview_round"))
    if actual_date != expected_date or actual_time != expected_time or actual_end != expected_time_end:
        raise InterviewReconciliationError(
            "SCHEDULE_MISMATCH", "Booking schedule does not match expected invite evidence."
        )
    if actual_round != expected_round:
        raise InterviewReconciliationError(
            "ROUND_MISMATCH", "Booking round does not match expected invite evidence."
        )
    if not _truthy(booking.get("slot_confirmed")):
        raise InterviewReconciliationError(
            "BOOKING_NOT_CONFIRMED", "Booking is not confirmed."
        )
    actual_hash = booking_protected_hash(booking)
    if actual_hash != expected_booking_hash:
        raise InterviewReconciliationError(
            "BOOKING_HASH_MISMATCH", "Protected booking fields changed before reconciliation."
        )
    return actual_hash


def reconcile_interview_message_to_booking(
    *,
    mailbox_message_id: str,
    gmail_message_id: str,
    booking_id: str,
    expected_candidate_name: str,
    expected_date: str,
    expected_time: str,
    expected_time_end: str,
    expected_round: str,
    expected_booking_hash: str,
    company_name: str,
    reconciled_by: str,
) -> dict[str, Any]:
    """Atomically terminalize one recovered mail and link existing evidence.

    The booking row and failed analysis are locked/read-only invariants.  The
    only mutations are one interview evidence relationship, the exact mailbox
    message state, and one explicit recruitment audit entry.
    """
    required = {
        "mailbox_message_id": mailbox_message_id,
        "gmail_message_id": gmail_message_id,
        "booking_id": booking_id,
        "expected_candidate_name": expected_candidate_name,
        "expected_booking_hash": expected_booking_hash,
        "company_name": company_name,
        "reconciled_by": reconciled_by,
    }
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        raise InterviewReconciliationError(
            "MISSING_REQUIRED_FIELD", f"Missing reconciliation field: {missing[0]}."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_booking_hash.strip().lower()):
        raise InterviewReconciliationError(
            "INVALID_BOOKING_HASH", "Expected booking hash must be SHA-256."
        )

    expected_date = _normal_date(expected_date)
    expected_time = _normal_time(expected_time, field="start time")
    expected_time_end = _normal_time(expected_time_end, field="end time")
    expected_round = candidate_store.normalise_interview_round(expected_round)
    if not expected_round:
        raise InterviewReconciliationError("INVALID_ROUND", "Expected interview round is invalid.")

    preliminary_candidate_id = _preliminary_candidate_id(
        mailbox_message_id, gmail_message_id
    )

    # Use the same raw mailbox candidate lock key as execute_auto_booking().
    # Canonical identity is verified inside the transaction, but changing the
    # advisory key here would allow the normal worker to run concurrently for
    # a legacy/linked alias.
    with mail_store.candidate_booking_lock(preliminary_candidate_id):
        with get_connection() as conn, conn.cursor() as cur:
            message = _lock_message(cur, mailbox_message_id, gmail_message_id)
            booking = _lock_booking(cur, booking_id)
            if str(message.get("candidate_id") or "") != preliminary_candidate_id:
                raise InterviewReconciliationError(
                    "MESSAGE_CANDIDATE_CHANGED",
                    "Mailbox message candidate changed before its advisory lock was acquired.",
                )
            message_candidate_id = str(message.get("candidate_id") or "")
            booking_candidate_id = str(booking.get("id") or booking_id)
            if not _same_identity(cur, message_candidate_id, booking_candidate_id):
                raise InterviewReconciliationError(
                    "CANDIDATE_MISMATCH",
                    "Mailbox message and booking belong to different candidate identities.",
                )
            message_canonical = _canonical_identity(cur, message_candidate_id)

            before_hash = _validate_booking(
                booking,
                expected_candidate_name=expected_candidate_name,
                expected_date=expected_date,
                expected_time=expected_time,
                expected_time_end=expected_time_end,
                expected_round=expected_round,
                expected_booking_hash=expected_booking_hash.strip().lower(),
            )
            failed_analysis, failed_analysis_hash = _failed_analysis(
                cur, mailbox_message_id
            )
            failed_analysis_id = str(failed_analysis.get("id") or "")
            if not failed_analysis_id:
                raise InterviewReconciliationError(
                    "FAILED_ANALYSIS_NOT_FOUND", "Failed analysis identity is missing."
                )

            existing = _relationship_for_message(cur, mailbox_message_id)
            if existing and existing.get("reconciled_booking_id") not in {None, "", booking_id}:
                raise InterviewReconciliationError(
                    "RECONCILIATION_CONFLICT",
                    "Message is already reconciled to a different booking.",
                )
            status = str(message.get("processing_status") or "").upper()
            if status == TERMINAL_MESSAGE_STATUS:
                if not existing or existing.get("reconciled_booking_id") != booking_id:
                    raise InterviewReconciliationError(
                        "TERMINAL_RELATIONSHIP_MISSING",
                        "Terminal message does not have the expected booking relationship.",
                    )
                if not _audit_exists(cur, str(existing.get("id") or "")):
                    raise InterviewReconciliationError(
                        "RECONCILIATION_AUDIT_MISSING",
                        "Terminal reconciliation is missing its audit entry.",
                    )
                if message.get("ai_retry_after") is not None or message.get(
                    "ai_lease_expires_at"
                ) is not None:
                    raise InterviewReconciliationError(
                        "TERMINAL_RETRY_STATE_PRESENT",
                        "Terminal reconciliation still has retry or lease state.",
                    )
                final_booking = _lock_booking(cur, booking_id)
                final_hash = booking_protected_hash(final_booking)
                final_analysis, final_analysis_hash = _failed_analysis(cur, mailbox_message_id)
                if final_hash != before_hash or final_analysis_hash != failed_analysis_hash:
                    raise InterviewReconciliationError(
                        "IMMUTABILITY_CHECK_FAILED",
                        "Protected booking or failed analysis changed during reconciliation.",
                    )
                return {
                    "created": False,
                    "relationship_id": existing.get("id"),
                    "mailbox_message_id": mailbox_message_id,
                    "gmail_message_id": gmail_message_id,
                    "booking_id": booking_id,
                    "message_status": TERMINAL_MESSAGE_STATUS,
                    "reconciliation_status": RECONCILIATION_STATUS,
                    "booking_hash_before": before_hash,
                    "booking_hash_after": final_hash,
                    "failed_analysis_id": final_analysis.get("id"),
                    "failed_analysis_hash": final_analysis_hash,
                }
            if status not in RECONCILABLE_MESSAGE_STATUSES:
                raise InterviewReconciliationError(
                    "MESSAGE_NOT_RECONCILABLE",
                    f"Mailbox message status {status or 'UNKNOWN'} cannot be reconciled.",
                )

            relationship_id = str((existing or {}).get("id") or uuid.uuid4())
            relationship = _upsert_relationship(
                cur,
                relationship_id=relationship_id,
                message=message,
                canonical_candidate_id=message_canonical,
                failed_analysis_id=failed_analysis_id,
                booking_id=booking_id,
                company_name=company_name.strip(),
                expected_date=expected_date,
                expected_time=expected_time,
                expected_time_end=expected_time_end,
                expected_round=expected_round,
                booking_hash=before_hash,
                reconciled_by=reconciled_by.strip(),
            )
            terminal_message = _terminalize_message(cur, message)
            audit_id = _write_audit(
                cur,
                relationship_id=str(relationship.get("id") or relationship_id),
                canonical_candidate_id=message_canonical,
                message=message,
                booking_id=booking_id,
                booking_hash=before_hash,
                company_name=company_name.strip(),
                reconciled_by=reconciled_by.strip(),
            )

            final_booking = _lock_booking(cur, booking_id)
            final_hash = booking_protected_hash(final_booking)
            final_analysis, final_analysis_hash = _failed_analysis(cur, mailbox_message_id)
            if final_hash != before_hash:
                raise InterviewReconciliationError(
                    "BOOKING_IMMUTABILITY_FAILED",
                    "Protected booking fields changed during reconciliation.",
                )
            if final_analysis_hash != failed_analysis_hash:
                raise InterviewReconciliationError(
                    "FAILED_ANALYSIS_IMMUTABILITY_FAILED",
                    "Original failed analysis changed during reconciliation.",
                )
            if terminal_message.get("processing_status") != TERMINAL_MESSAGE_STATUS:
                raise InterviewReconciliationError(
                    "TERMINALIZATION_FAILED",
                    "Mailbox message did not reach the reconciled terminal state.",
                )
            return {
                "created": existing is None,
                "relationship_id": relationship.get("id"),
                "audit_id": audit_id,
                "mailbox_message_id": mailbox_message_id,
                "gmail_message_id": gmail_message_id,
                "booking_id": booking_id,
                "message_status": terminal_message.get("processing_status"),
                "previous_message_status": terminal_message.get(
                    "previous_processing_status"
                ),
                "reconciliation_status": relationship.get("reconciliation_status"),
                "booking_hash_before": before_hash,
                "booking_hash_after": final_hash,
                "failed_analysis_id": final_analysis.get("id"),
                "failed_analysis_hash": final_analysis_hash,
            }
