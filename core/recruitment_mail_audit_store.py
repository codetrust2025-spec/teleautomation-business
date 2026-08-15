"""Persistence and orchestration for the candidate mail outcome audit.

The audit is read-only over mail: it reads what Gmail sync already stored,
re-derives each outcome from the evidence, compares that against what the live
pipeline concluded, and records the difference.  It never sends, deletes,
labels or modifies a message, and it never writes candidate status on its own —
that requires an explicit administrator approval recorded in
``mail_outcome_audit_approvals``.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.db.connection import get_connection, use_postgres
from core import recruitment_mail_audit as engine
from core import recruitment_mail_store as store

logger = logging.getLogger("teleautomation.recruitment_mail_audit")

REPORT_ONLY = "REPORT_ONLY"
APPLY_APPROVED = "APPLY_APPROVED"

# Gap taxonomy — every reason a relevant mail can fail to become a system event.
GAP_SYNC_FAILURE = "SYNC_FAILURE"
GAP_SYNC_INCOMPLETE = "SYNC_INCOMPLETE"
GAP_AI_QUEUE_FAILURE = "AI_QUEUE_FAILURE"
GAP_MISSING_EVENT = "MISSING_EVENT"
GAP_MISCLASSIFIED = "MISCLASSIFIED"
GAP_LOW_CONFIDENCE = "LOW_CONFIDENCE"
GAP_ATTACHMENT_EXTRACTION_FAILED = "ATTACHMENT_EXTRACTION_FAILED"
GAP_DEDUP_SUPPRESSED = "DEDUP_SUPPRESSED"
GAP_SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
GAP_PROCESSING_EXCEPTION = "PROCESSING_EXCEPTION"
GAP_HISTORICAL_LIMIT = "HISTORICAL_RESCAN_LIMIT"
GAP_CANDIDATE_MAPPING = "CANDIDATE_MAPPING"

# Live-pipeline statuses mapped onto the audit taxonomy so the two can be
# compared.  Anything unmapped compares as "no outcome".
_PIPELINE_TO_AUDIT = {
    "JOINED": engine.JOINING_CONFIRMED,
    "JOINING_CONFIRMED": engine.JOINING_CONFIRMED,
    "JOINING_DATE_UPDATED": engine.JOINING_CONFIRMED,
    "POST_SELECTION_ONBOARDING": engine.JOINING_CONFIRMED,
    "OFFER_LETTER_RECEIVED": engine.VERIFIED_OFFER_LETTER,
    "APPOINTMENT_LETTER_RECEIVED": engine.VERIFIED_OFFER_LETTER,
    "OFFER_ACCEPTED": engine.VERIFIED_OFFER_LETTER,
    "OFFER_INDICATION": engine.OFFER_INDICATION,
    "OFFER_IN_PROGRESS": engine.OFFER_INDICATION,
    "OFFER_APPROVED": engine.OFFER_INDICATION,
    "COMPENSATION_CONFIRMATION": engine.OFFER_INDICATION,
    "OFFER_DECLINED": engine.OFFER_INDICATION,
    "OFFER_REVOKED": engine.OFFER_INDICATION,
    "SELECTED": engine.FINAL_SELECTION,
    "FINAL_SELECTION_CONFIRMED": engine.FINAL_SELECTION,
    "BACKGROUND_VERIFICATION": engine.BACKGROUND_VERIFICATION,
    "DOCUMENT_VERIFICATION": engine.BACKGROUND_VERIFICATION,
    "INTERVIEW_SHORTLISTED": engine.SHORTLISTED,
    "INTERVIEW_CONFIRMED": engine.INTERVIEW_INVITE,
    "INTERVIEW_UPDATE": engine.INTERVIEW_INVITE,
    # A proposed interview is an invite whose booking detail the backend did not
    # trust enough to confirm — informational, same audit family as an invite.
    "INTERVIEW_PROPOSED": engine.INTERVIEW_INVITE,
    "OFFER_NEEDS_REVIEW": engine.OFFER_INDICATION,
    "JOINING_NEEDS_REVIEW": engine.OFFER_INDICATION,
    "SELECTION_NEEDS_REVIEW": engine.SHORTLISTED,
    "INTERVIEW_RESCHEDULED": engine.INTERVIEW_RESCHEDULED,
    "INTERVIEW_CANCELLED": engine.INTERVIEW_CANCELLED,
    "CANDIDATE_REJECTED": engine.REJECTED,
    "MANUAL_REVIEW_REQUIRED": engine.MANUAL_REVIEW_REQUIRED,
    "IGNORED_LOW_CONFIDENCE": engine.NOT_RELEVANT,
    "IGNORED_NOT_OFFER_RELATED": engine.NOT_RELEVANT,
}

# Audit outcome -> the candidate status an administrator may approve.  Kept
# deliberately narrow: interview scheduling and document requests never move a
# candidate's hiring status.
_AUDIT_TO_CANDIDATE_STATUS = {
    engine.JOINING_CONFIRMED: "Joining Confirmed",
    engine.VERIFIED_OFFER_LETTER: "Offer Received",
    engine.OFFER_INDICATION: "Offer Received",
    engine.FINAL_SELECTION: "Selected",
    engine.BACKGROUND_VERIFICATION: "Selected",
    engine.SHORTLISTED: "Interview Shortlisted",
    engine.NEXT_ROUND: "Interview In Progress",
    engine.INTERVIEW_INVITE: "Interview Confirmed",
    engine.INTERVIEW_RESCHEDULED: "Interview Rescheduled",
    engine.INTERVIEW_CANCELLED: "Interview Cancelled",
    engine.REJECTED: "Rejected",
}

_MIN_CONFIDENCE_FOR_GAP = float(os.getenv("AI_MAIL_AUDIT_GAP_MIN_CONFIDENCE", "70"))


def _id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema() -> None:
    if not use_postgres():
        return
    migrations = Path(__file__).with_name("migrations")
    with get_connection() as conn, conn.cursor() as cur:
        for name in ("019_recruitment_mail_outcome_audit.sql",
                     "020_recruitment_mail_audit_cleanup.sql",
                     "021_recruitment_mail_audit_provenance.sql"):
            cur.execute((migrations / name).read_text(encoding="utf-8"))


def _rows(cur) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


# ── Mailbox selection ────────────────────────────────────────────────────────

def authorized_mailboxes(*, candidate_id: str | None = None) -> list[dict[str, Any]]:
    """Mailboxes the operator explicitly connected and authorized.

    Only rows holding stored OAuth credentials qualify.  A mailbox that was
    superseded by a re-connect is excluded so one candidate is audited once.
    """
    clauses = [
        "m.credential_ciphertext IS NOT NULL",
        "m.connection_status <> 'SUPERSEDED'",
    ]
    params: list[Any] = []
    if candidate_id:
        clauses.append(
            "(m.candidate_id=%s OR COALESCE(l.canonical_candidate_id,m.candidate_id)=%s)"
        )
        params.extend([candidate_id, candidate_id])
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT m.id,m.candidate_id,
                       COALESCE(l.canonical_candidate_id,m.candidate_id) AS canonical_candidate_id,
                       m.email_address,m.provider,m.connection_status,m.monitoring_enabled,
                       m.last_successful_sync_at,m.last_sync_attempt_at,m.failed_sync_count,
                       m.last_error_code,m.last_error_message,m.created_at
                FROM candidate_mailboxes m
                LEFT JOIN candidate_identity_links l ON l.alias_candidate_id=m.candidate_id
                WHERE {' AND '.join(clauses)}
                ORDER BY m.created_at""",
            params,
        )
        return _rows(cur)


def _candidate_name(candidate_id: str) -> str | None:
    try:
        from features import candidate_store
        row = candidate_store.get_candidate(candidate_id) or {}
        return row.get("name")
    except Exception:
        return None


# ── Message loading ──────────────────────────────────────────────────────────

def _messages_for_mailbox(mailbox_id: str, *, since: datetime | None = None) -> list[dict[str, Any]]:
    clauses = ["m.mailbox_id=%s"]
    params: list[Any] = [mailbox_id]
    if since is not None:
        clauses.append("m.created_at > %s")
        params.append(since)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT m.id,m.mailbox_id,m.candidate_id,m.provider_message_id,m.provider_thread_id,
                       m.rfc_message_id,m.sender_name,m.sender_email,m.recipient_email,m.subject,
                       m.sent_at,m.body_text,m.html_body_text,m.authentication_results,m.received_spf,
                       m.reply_to_email,m.return_path_email,m.message_direction,m.gmail_label_ids,
                       m.processing_status,m.ignore_reason,m.ai_retry_count,m.ai_last_error_code,
                       m.recruitment_relevance_score,m.created_at
                FROM mailbox_messages m
                WHERE {' AND '.join(clauses)}
                ORDER BY COALESCE(m.sent_at,m.created_at)""",
            params,
        )
        messages = _rows(cur)
    if not messages:
        return []
    ids = [row["id"] for row in messages]
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.mailbox_message_id,a.filename,a.mime_type,a.size,a.checksum,
                      a.attachment_type,a.extraction_status,c.extracted_text
               FROM mailbox_attachments a
               LEFT JOIN mailbox_attachment_cache c ON c.checksum=a.checksum
               WHERE a.mailbox_message_id = ANY(%s)""",
            (ids,),
        )
        attachments = _rows(cur)
        cur.execute(
            """SELECT e.id,e.mailbox_message_id,e.primary_status,e.confidence,e.company_name,
                      e.company_domain,e.job_title,e.review_status,e.requires_manual_review,
                      e.structured_result
               FROM ai_recruitment_events e
               WHERE e.mailbox_message_id = ANY(%s)""",
            (ids,),
        )
        events = _rows(cur)

    by_message: dict[str, list[dict[str, Any]]] = {}
    for item in attachments:
        by_message.setdefault(item["mailbox_message_id"], []).append({
            "filename": item.get("filename"),
            "mime_type": item.get("mime_type"),
            "size": item.get("size"),
            "checksum": item.get("checksum"),
            "attachment_type": item.get("attachment_type"),
            "extraction_status": item.get("extraction_status"),
            "text": item.get("extracted_text") or "",
        })
    event_by_message = {item["mailbox_message_id"]: item for item in events}

    threads: dict[str, list[dict[str, Any]]] = {}
    for row in messages:
        thread_id = row.get("provider_thread_id")
        if thread_id:
            threads.setdefault(thread_id, []).append(row)

    for row in messages:
        row["attachments"] = by_message.get(row["id"], [])
        row["pipeline_event"] = event_by_message.get(row["id"])
        siblings = threads.get(row.get("provider_thread_id") or "", [])
        row["thread_context"] = [
            {"subject": item.get("subject"), "body": item.get("body_text")}
            for item in siblings if item["id"] != row["id"]
        ][-5:]
    return messages


# ── Per-message audit ────────────────────────────────────────────────────────

def audit_message(message: dict[str, Any], mailbox: dict[str, Any]) -> dict[str, Any]:
    """Classify one stored message and compare against the pipeline result."""
    attachments = message.get("attachments") or []
    payload = {
        "subject": message.get("subject"),
        "body": message.get("body_text"),
        "html_body": message.get("html_body_text"),
        "sender_email": message.get("sender_email"),
        "sender_name": message.get("sender_name"),
        "authentication_results": message.get("authentication_results"),
        "received_spf": message.get("received_spf"),
        "reply_to_email": message.get("reply_to_email"),
        "return_path_email": message.get("return_path_email"),
        "message_direction": message.get("message_direction"),
    }
    verdict = engine.classify_message(
        payload, attachments, thread_context=message.get("thread_context")
    )

    event = message.get("pipeline_event") or {}
    structured = event.get("structured_result") or {}
    if isinstance(structured, str):
        try:
            import json
            structured = json.loads(structured)
        except Exception:
            structured = {}
    company_name = event.get("company_name") or (structured.get("company") or {}).get("name")
    company_domain = event.get("company_domain") or (structured.get("company") or {}).get("domain")
    if not company_domain:
        company_domain = engine.domain_of(message.get("sender_email"))

    authenticity = engine.assess_authenticity(
        payload,
        company_domain=company_domain,
        mailbox_email=mailbox.get("email_address"),
        attachments=attachments,
    )

    pipeline_status = str(event.get("primary_status") or "").upper()
    pipeline_outcome = _PIPELINE_TO_AUDIT.get(pipeline_status) if pipeline_status else None

    outcome = verdict["outcome"]
    if outcome in engine.MEANINGFUL_OUTCOMES and authenticity["verdict"] == engine.AUTHENTICITY_SUSPICIOUS:
        # Suspicious provenance never silently downgrades a real outcome; it
        # routes the finding to a human with the concerns attached.
        verdict = dict(verdict)
        verdict["manual_review_required"] = True
        verdict["rationale"] = (
            verdict["rationale"] + " Authenticity concerns require review: "
            + "; ".join(authenticity["concerns"])
        )

    if pipeline_outcome is None:
        agreement = "NO_PIPELINE_RESULT"
    elif pipeline_outcome == outcome:
        agreement = "AGREE"
    elif engine.OUTCOME_RANK.get(pipeline_outcome, 0) > engine.OUTCOME_RANK.get(outcome, 0):
        agreement = "PIPELINE_STRONGER"
    else:
        agreement = "AUDIT_STRONGER"

    job_title = event.get("job_title") or (structured.get("job") or {}).get("title")
    source_type = engine.classify_source(message.get("sender_email"), company_domain)
    bulk = "BULK_CAMPAIGN" in (verdict.get("signals") or [])
    has_document = any(
        str(item.get("attachment_type") or "").upper() in {
            "OFFER_LETTER", "APPOINTMENT_LETTER", "JOINING_LETTER"}
        and item.get("text")
        for item in attachments
    )
    strength = engine.evidence_strength(
        source=source_type, authenticity=authenticity["verdict"], bulk=bulk,
        outcome=verdict["outcome"], has_attachment_proof=has_document,
    )

    return {
        "mailbox_id": mailbox["id"],
        "candidate_id": message.get("candidate_id") or mailbox.get("candidate_id"),
        "canonical_candidate_id": mailbox.get("canonical_candidate_id") or mailbox.get("candidate_id"),
        "mailbox_message_id": message.get("id"),
        "provider_message_id": message.get("provider_message_id"),
        "provider_thread_id": message.get("provider_thread_id"),
        "rfc_message_id": message.get("rfc_message_id"),
        "calendar_uid": engine.calendar_uid(attachments),
        "attachment_fingerprint": engine.attachment_fingerprint(attachments),
        "subject": message.get("subject"),
        "sender_name": message.get("sender_name"),
        "sender_email": message.get("sender_email"),
        "sender_domain": authenticity["sender_domain"],
        "received_at": message.get("sent_at") or message.get("created_at"),
        "company_name": company_name,
        "company_domain": company_domain,
        "job_title": job_title,
        "source_type": source_type,
        "evidence_strength": strength,
        "application_key": engine.application_key({
            "company_domain": company_domain, "company_name": company_name,
            "sender_domain": authenticity["sender_domain"], "job_title": job_title,
        }),
        "outcome": verdict["outcome"],
        "outcome_rank": verdict["outcome_rank"],
        "confidence": verdict["confidence"],
        "rationale": verdict["rationale"],
        "evidence": verdict["evidence"],
        "attachment_evidence": [
            {
                "filename": item.get("filename"),
                "mime_type": item.get("mime_type"),
                "attachment_type": item.get("attachment_type"),
                "extraction_status": item.get("extraction_status"),
                "checksum": item.get("checksum"),
                "has_text": bool(item.get("text")),
            }
            for item in attachments
        ],
        "authenticity": authenticity["verdict"],
        "authenticity_detail": authenticity,
        "manual_review_required": verdict["manual_review_required"],
        "pipeline_outcome": pipeline_outcome,
        "pipeline_event_id": event.get("id"),
        "pipeline_agreement": agreement,
        "content_signature": engine.content_signature(payload, attachments),
        "_processing_status": str(message.get("processing_status") or "").upper(),
        "_ignore_reason": message.get("ignore_reason"),
        "_ai_error": message.get("ai_last_error_code"),
        "_message_candidate_id": message.get("candidate_id"),
    }


# ── Gap detection ────────────────────────────────────────────────────────────

def _mailbox_gaps(mailbox: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Everything the pipeline should have handled for this mailbox but did not."""
    gaps: list[dict[str, Any]] = []
    canonical = mailbox.get("canonical_candidate_id") or mailbox.get("candidate_id")

    def add(gap_type: str, *, severity: str, detail: str,
            provider_message_id: str | None = None, mailbox_message_id: str | None = None,
            audit_outcome: str | None = None, pipeline_outcome: str | None = None,
            metadata: dict[str, Any] | None = None) -> None:
        gaps.append({
            "mailbox_id": mailbox["id"], "canonical_candidate_id": canonical,
            "provider_message_id": provider_message_id,
            "mailbox_message_id": mailbox_message_id,
            "gap_type": gap_type, "severity": severity, "detail": detail,
            "audit_outcome": audit_outcome, "pipeline_outcome": pipeline_outcome,
            "metadata": metadata or {},
        })

    if str(mailbox.get("connection_status") or "").upper() == "ERROR":
        add(GAP_SYNC_FAILURE, severity="HIGH",
            detail=f"Mailbox connection is in ERROR: {mailbox.get('last_error_code') or 'unknown'} "
                   f"{str(mailbox.get('last_error_message') or '')[:200]}".strip())
    if not mailbox.get("last_successful_sync_at"):
        add(GAP_SYNC_FAILURE, severity="HIGH",
            detail="Mailbox has never completed a successful sync; stored mail may be incomplete.")

    # Gmail ids discovered but never ingested.
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT status,count(*) AS total,min(discovered_at) AS oldest
               FROM gmail_message_ingestion_queue
               WHERE mailbox_id=%s AND status IN ('QUEUED','RUNNING','DEAD_LETTER')
               GROUP BY status""",
            (mailbox["id"],),
        )
        pending = _rows(cur)
    for row in pending:
        severity = "HIGH" if row["status"] == "DEAD_LETTER" else "MEDIUM"
        add(GAP_SYNC_INCOMPLETE, severity=severity,
            detail=f"{row['total']} Gmail message(s) in {row['status']} since {row['oldest']}; "
                   "their content has not been audited.",
            metadata={"status": row["status"], "count": int(row["total"])})

    # Historical coverage: mail older than the first stored message is invisible.
    stored_dates = [f.get("received_at") for f in findings if f.get("received_at")]
    if stored_dates and mailbox.get("created_at"):
        earliest = min(d for d in stored_dates if d)
        created = mailbox["created_at"]
        try:
            gap_days = (earliest - created).days
        except TypeError:
            gap_days = 0
        if gap_days > 1:
            add(GAP_HISTORICAL_LIMIT, severity="LOW",
                detail=f"Oldest audited mail is {earliest:%Y-%m-%d}, {gap_days} day(s) after the mailbox "
                       "was connected. Older mail needs a historical rescan to be audited.",
                metadata={"earliest": str(earliest), "connected_at": str(created)})

    seen_signatures: dict[str, str] = {}
    mailbox_candidate = str(mailbox.get("candidate_id") or "")
    for finding in findings:
        status = finding.get("_processing_status") or ""

        # A message stored under a different candidate than the mailbox it was
        # fetched from means the outcome would land on the wrong person.
        message_candidate = str(finding.get("_message_candidate_id") or "")
        if message_candidate and mailbox_candidate and message_candidate != mailbox_candidate:
            add(GAP_CANDIDATE_MAPPING, severity="HIGH",
                detail=f"Message is stored against candidate {message_candidate} but was fetched from "
                       f"the mailbox of candidate {mailbox_candidate}.",
                provider_message_id=finding.get("provider_message_id"),
                mailbox_message_id=finding.get("mailbox_message_id"),
                audit_outcome=finding.get("outcome"),
                metadata={"message_candidate_id": message_candidate,
                          "mailbox_candidate_id": mailbox_candidate})

        outcome = finding["outcome"]
        meaningful = outcome in engine.MEANINGFUL_OUTCOMES
        confident = float(finding.get("confidence") or 0) >= _MIN_CONFIDENCE_FOR_GAP
        pid = finding.get("provider_message_id")
        mid = finding.get("mailbox_message_id")

        if status in {"AI_RETRY_PENDING", "AI_FAILED"}:
            add(GAP_AI_QUEUE_FAILURE, severity="HIGH" if meaningful else "MEDIUM",
                detail=f"Message is stuck in {status}"
                       + (f" ({finding.get('_ai_error')})" if finding.get("_ai_error") else "")
                       + ("; the audit reads it as " + outcome if meaningful else ""),
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome)
            continue
        if status in {"FAILED", "ERROR"}:
            add(GAP_PROCESSING_EXCEPTION, severity="HIGH" if meaningful else "LOW",
                detail=f"Message processing ended in {status}.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome)
            continue
        if status == "VALIDATION_FAILED":
            add(GAP_SCHEMA_VALIDATION_FAILED, severity="MEDIUM",
                detail="AI response failed schema validation, so no event was created.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome)
            continue

        # Attachment text the audit could not read.
        for item in finding.get("attachment_evidence") or []:
            if str(item.get("extraction_status") or "").upper() in {"FAILED", "ERROR", "UNSUPPORTED"}:
                add(GAP_ATTACHMENT_EXTRACTION_FAILED,
                    severity="HIGH" if meaningful else "LOW",
                    detail=f"Attachment {item.get('filename')} could not be extracted "
                           f"({item.get('extraction_status')}); its contents are unaudited.",
                    provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome,
                    metadata={"filename": item.get("filename")})

        if not meaningful:
            continue

        signature = finding.get("content_signature") or ""
        if signature and signature in seen_signatures:
            add(GAP_DEDUP_SUPPRESSED, severity="LOW",
                detail="Identical content already audited under message "
                       f"{seen_signatures[signature]}; counted once.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome,
                metadata={"duplicate_of": seen_signatures[signature]})
        elif signature:
            seen_signatures[signature] = str(pid)

        if status in {"DUPLICATE", "DUPLICATE_CONTENT", "IGNORED"} and confident:
            add(GAP_DEDUP_SUPPRESSED, severity="MEDIUM",
                detail=f"Pipeline suppressed this message as {status}"
                       + (f" ({finding.get('_ignore_reason')})" if finding.get("_ignore_reason") else "")
                       + f", but the audit reads it as {outcome}.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome)
            continue

        agreement = finding.get("pipeline_agreement")
        if agreement == "NO_PIPELINE_RESULT" and confident:
            add(GAP_MISSING_EVENT, severity="HIGH",
                detail=f"The audit reads this mail as {outcome} but no recruitment event exists.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome)
        elif agreement == "AUDIT_STRONGER" and confident:
            add(GAP_MISCLASSIFIED, severity="MEDIUM",
                detail=f"Pipeline recorded {finding.get('pipeline_outcome')} where the audit reads {outcome}.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome,
                pipeline_outcome=finding.get("pipeline_outcome"))
        elif agreement == "PIPELINE_STRONGER":
            add(GAP_MISCLASSIFIED, severity="HIGH",
                detail=f"Pipeline recorded {finding.get('pipeline_outcome')}, stronger than the "
                       f"audit's {outcome}. The system status may overstate this candidate's progress.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome,
                pipeline_outcome=finding.get("pipeline_outcome"))
        elif meaningful and not confident:
            add(GAP_LOW_CONFIDENCE, severity="LOW",
                detail=f"Evidence for {outcome} is below the reporting confidence threshold.",
                provider_message_id=pid, mailbox_message_id=mid, audit_outcome=outcome)

    return gaps


# ── Persistence ──────────────────────────────────────────────────────────────

def _json(value: Any) -> str:
    import json
    return json.dumps(value, default=str)


def _persist_findings(run_id: str, findings: list[dict[str, Any]]) -> int:
    """Upsert findings, keeping an append-only trail when an outcome changes."""
    if not findings:
        return 0
    written = 0
    with get_connection() as conn, conn.cursor() as cur:
        for finding in findings:
            cur.execute(
                """SELECT id,outcome,confidence FROM mail_outcome_audit_findings
                   WHERE mailbox_id=%s AND provider_message_id=%s""",
                (finding["mailbox_id"], finding["provider_message_id"]),
            )
            existing = cur.fetchone()
            if existing:
                finding_id, previous_outcome, previous_confidence = existing
                cur.execute(
                    """UPDATE mail_outcome_audit_findings SET
                         run_id=%s,mailbox_message_id=%s,provider_thread_id=%s,rfc_message_id=%s,
                         calendar_uid=%s,attachment_fingerprint=%s,subject=%s,sender_name=%s,
                         sender_email=%s,sender_domain=%s,received_at=%s,company_name=%s,
                         company_domain=%s,job_title=%s,outcome=%s,outcome_rank=%s,confidence=%s,
                         rationale=%s,evidence=%s::jsonb,attachment_evidence=%s::jsonb,
                         authenticity=%s,authenticity_detail=%s::jsonb,manual_review_required=%s,
                         pipeline_outcome=%s,pipeline_event_id=%s,pipeline_agreement=%s,
                         content_signature=%s,source_type=%s,evidence_strength=%s,
                         application_key=%s,last_seen_at=now(),updated_at=now()
                       WHERE id=%s""",
                    (run_id, finding["mailbox_message_id"], finding["provider_thread_id"],
                     finding["rfc_message_id"], finding["calendar_uid"],
                     finding["attachment_fingerprint"], finding["subject"], finding["sender_name"],
                     finding["sender_email"], finding["sender_domain"], finding["received_at"],
                     finding["company_name"], finding["company_domain"], finding["job_title"],
                     finding["outcome"], finding["outcome_rank"], finding["confidence"],
                     finding["rationale"], _json(finding["evidence"]),
                     _json(finding["attachment_evidence"]), finding["authenticity"],
                     _json(finding["authenticity_detail"]), finding["manual_review_required"],
                     finding["pipeline_outcome"], finding["pipeline_event_id"],
                     finding["pipeline_agreement"], finding["content_signature"],
                     finding["source_type"], finding["evidence_strength"],
                     finding["application_key"], finding_id),
                )
                if str(previous_outcome) != finding["outcome"]:
                    cur.execute(
                        """INSERT INTO mail_outcome_audit_finding_history
                             (id,finding_id,run_id,previous_outcome,new_outcome,
                              previous_confidence,new_confidence,reason)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (_id(), finding_id, run_id, previous_outcome, finding["outcome"],
                         previous_confidence, finding["confidence"],
                         "Outcome changed on re-audit."),
                    )
                finding["id"] = finding_id
            else:
                finding_id = _id()
                cur.execute(
                    """INSERT INTO mail_outcome_audit_findings
                         (id,run_id,mailbox_id,candidate_id,canonical_candidate_id,
                          mailbox_message_id,provider_message_id,provider_thread_id,rfc_message_id,
                          calendar_uid,attachment_fingerprint,subject,sender_name,sender_email,
                          sender_domain,received_at,company_name,company_domain,job_title,
                          outcome,outcome_rank,confidence,rationale,evidence,attachment_evidence,
                          authenticity,authenticity_detail,manual_review_required,pipeline_outcome,
                          pipeline_event_id,pipeline_agreement,content_signature,
                          source_type,evidence_strength,application_key)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (finding_id, run_id, finding["mailbox_id"], finding["candidate_id"],
                     finding["canonical_candidate_id"], finding["mailbox_message_id"],
                     finding["provider_message_id"], finding["provider_thread_id"],
                     finding["rfc_message_id"], finding["calendar_uid"],
                     finding["attachment_fingerprint"], finding["subject"], finding["sender_name"],
                     finding["sender_email"], finding["sender_domain"], finding["received_at"],
                     finding["company_name"], finding["company_domain"], finding["job_title"],
                     finding["outcome"], finding["outcome_rank"], finding["confidence"],
                     finding["rationale"], _json(finding["evidence"]),
                     _json(finding["attachment_evidence"]), finding["authenticity"],
                     _json(finding["authenticity_detail"]), finding["manual_review_required"],
                     finding["pipeline_outcome"], finding["pipeline_event_id"],
                     finding["pipeline_agreement"], finding["content_signature"],
                     finding["source_type"], finding["evidence_strength"],
                     finding["application_key"]),
                )
                cur.execute(
                    """INSERT INTO mail_outcome_audit_finding_history
                         (id,finding_id,run_id,previous_outcome,new_outcome,
                          previous_confidence,new_confidence,reason)
                       VALUES (%s,%s,%s,NULL,%s,NULL,%s,%s)""",
                    (_id(), finding_id, run_id, finding["outcome"], finding["confidence"],
                     "First audit of this message."),
                )
                finding["id"] = finding_id
            written += 1
    return written


def _persist_gaps(run_id: str, gaps: list[dict[str, Any]]) -> int:
    if not gaps:
        return 0
    with get_connection() as conn, conn.cursor() as cur:
        for gap in gaps:
            cur.execute(
                """INSERT INTO mail_outcome_audit_gaps
                     (id,run_id,mailbox_id,canonical_candidate_id,provider_message_id,
                      mailbox_message_id,gap_type,severity,detail,audit_outcome,
                      pipeline_outcome,metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (mailbox_id,gap_type,COALESCE(provider_message_id,'')) DO UPDATE SET
                     run_id=EXCLUDED.run_id,severity=EXCLUDED.severity,detail=EXCLUDED.detail,
                     audit_outcome=EXCLUDED.audit_outcome,pipeline_outcome=EXCLUDED.pipeline_outcome,
                     metadata=EXCLUDED.metadata,updated_at=now()""",
                (_id(), run_id, gap["mailbox_id"], gap["canonical_candidate_id"],
                 gap["provider_message_id"], gap["mailbox_message_id"], gap["gap_type"],
                 gap["severity"], gap["detail"], gap["audit_outcome"],
                 gap["pipeline_outcome"], _json(gap["metadata"])),
            )
    return len(gaps)


def _system_status(canonical_candidate_id: str) -> tuple[str | None, str | None]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status,source FROM candidate_job_status WHERE candidate_id=%s",
            (canonical_candidate_id,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def build_candidate_rollup(
    mailbox: dict[str, Any], findings: list[dict[str, Any]], *,
    system_status: str | None, system_source: str | None,
    scan_status: str = "SCANNED", scan_error: str | None = None,
    messages_examined: int = 0, candidate_name: str | None = None,
) -> dict[str, Any]:
    """Summarise one candidate from their findings. Pure; no database access."""
    canonical = mailbox.get("canonical_candidate_id") or mailbox.get("candidate_id")
    relevant = [f for f in findings if f["outcome"] in engine.MEANINGFUL_OUTCOMES]
    best = engine.strongest(findings)
    conflicts = engine.detect_conflicts(findings)
    counts = engine.outcome_counts(findings)
    companies = sorted({
        str(f.get("company_name") or f.get("sender_domain") or "").strip()
        for f in relevant if (f.get("company_name") or f.get("sender_domain"))
    })
    suspicious = any(f["authenticity"] == engine.AUTHENTICITY_SUSPICIOUS for f in relevant)
    manual = any(f["manual_review_required"] for f in findings) or bool(conflicts)

    latest = max(relevant, key=lambda f: str(f.get("received_at") or ""), default=None)

    strongest_outcome = best["outcome"] if best else engine.NOT_RELEVANT
    expected_status = _AUDIT_TO_CANDIDATE_STATUS.get(strongest_outcome)
    mismatch = False
    mismatch_detail = None
    if expected_status and system_status and expected_status != system_status:
        mismatch = True
        mismatch_detail = (
            f"Mail evidence supports '{expected_status}' ({strongest_outcome}); "
            f"TeleAutomation shows '{system_status}'."
        )
    elif expected_status and not system_status:
        mismatch = True
        mismatch_detail = (
            f"Mail evidence supports '{expected_status}' ({strongest_outcome}); "
            "TeleAutomation has no detected job status for this candidate."
        )
    elif not expected_status and system_status and system_status not in {"Profile Active", "Needs Review"}:
        mismatch = True
        mismatch_detail = (
            f"TeleAutomation shows '{system_status}' but the audit found no supporting mail evidence."
        )

    if conflicts:
        recommended = "Human review: conflicting outcomes for the same company."
    elif suspicious:
        recommended = "Human review: sender authenticity concerns on a material outcome."
    elif mismatch:
        recommended = f"Review and, if correct, approve the status update to '{expected_status}'." \
            if expected_status else "Review why the system status has no mail evidence."
    elif manual:
        recommended = "Human review: incomplete evidence on one or more messages."
    elif relevant:
        recommended = "No action; system status matches the mail evidence."
    else:
        recommended = "No meaningful outcome found in this mailbox."

    return {
        "canonical_candidate_id": str(canonical),
        "candidate_id": str(mailbox.get("candidate_id")),
        "candidate_name": candidate_name,
        "mailbox_id": mailbox["id"],
        "email_address": mailbox.get("email_address"),
        "monitoring_status": "MONITORING_ACTIVE" if mailbox.get("monitoring_enabled") else "MONITORING_PAUSED",
        "connection_status": mailbox.get("connection_status"),
        "last_successful_sync_at": mailbox.get("last_successful_sync_at"),
        "scan_status": scan_status,
        "scan_error": scan_error,
        "messages_examined": messages_examined,
        "relevant_messages": len(relevant),
        "companies": companies,
        "outcome_counts": counts,
        "strongest_outcome": strongest_outcome,
        "strongest_outcome_rank": engine.OUTCOME_RANK.get(strongest_outcome, 0),
        "strongest_finding_id": (best or {}).get("id"),
        "strongest_confidence": float((best or {}).get("confidence") or 0),
        "strongest_authenticity": (best or {}).get("authenticity"),
        "latest_outcome": (latest or {}).get("outcome"),
        "latest_outcome_at": (latest or {}).get("received_at"),
        "system_status": system_status,
        "system_status_source": system_source,
        "status_mismatch": mismatch,
        "mismatch_detail": mismatch_detail,
        "manual_review_required": manual,
        "conflicting_evidence": bool(conflicts),
        "suspicious_evidence": suspicious,
        "recommended_action": recommended,
    }


def _persist_candidate(run_id: str, mailbox: dict[str, Any], findings: list[dict[str, Any]],
                       *, scan_status: str, scan_error: str | None,
                       messages_examined: int) -> dict[str, Any]:
    canonical = str(mailbox.get("canonical_candidate_id") or mailbox.get("candidate_id"))
    system_status, system_source = _system_status(canonical)
    row = build_candidate_rollup(
        mailbox, findings, system_status=system_status, system_source=system_source,
        scan_status=scan_status, scan_error=scan_error, messages_examined=messages_examined,
        candidate_name=_candidate_name(str(mailbox.get("candidate_id"))),
    )
    companies = row["companies"]
    counts = row["outcome_counts"]

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_outcome_audit_candidates
                 (canonical_candidate_id,run_id,candidate_id,candidate_name,mailbox_id,email_address,
                  monitoring_status,connection_status,last_successful_sync_at,scan_status,scan_error,
                  messages_examined,relevant_messages,companies,outcome_counts,strongest_outcome,
                  strongest_outcome_rank,strongest_finding_id,strongest_confidence,strongest_authenticity,
                  latest_outcome,latest_outcome_at,system_status,system_status_source,status_mismatch,
                  mismatch_detail,manual_review_required,conflicting_evidence,suspicious_evidence,
                  recommended_action,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s,%s,%s,now())
               ON CONFLICT (canonical_candidate_id) DO UPDATE SET
                 run_id=EXCLUDED.run_id,candidate_id=EXCLUDED.candidate_id,
                 candidate_name=EXCLUDED.candidate_name,mailbox_id=EXCLUDED.mailbox_id,
                 email_address=EXCLUDED.email_address,monitoring_status=EXCLUDED.monitoring_status,
                 connection_status=EXCLUDED.connection_status,
                 last_successful_sync_at=EXCLUDED.last_successful_sync_at,
                 scan_status=EXCLUDED.scan_status,scan_error=EXCLUDED.scan_error,
                 messages_examined=EXCLUDED.messages_examined,
                 relevant_messages=EXCLUDED.relevant_messages,companies=EXCLUDED.companies,
                 outcome_counts=EXCLUDED.outcome_counts,strongest_outcome=EXCLUDED.strongest_outcome,
                 strongest_outcome_rank=EXCLUDED.strongest_outcome_rank,
                 strongest_finding_id=EXCLUDED.strongest_finding_id,
                 strongest_confidence=EXCLUDED.strongest_confidence,
                 strongest_authenticity=EXCLUDED.strongest_authenticity,
                 latest_outcome=EXCLUDED.latest_outcome,latest_outcome_at=EXCLUDED.latest_outcome_at,
                 system_status=EXCLUDED.system_status,system_status_source=EXCLUDED.system_status_source,
                 status_mismatch=EXCLUDED.status_mismatch,mismatch_detail=EXCLUDED.mismatch_detail,
                 manual_review_required=EXCLUDED.manual_review_required,
                 conflicting_evidence=EXCLUDED.conflicting_evidence,
                 suspicious_evidence=EXCLUDED.suspicious_evidence,
                 recommended_action=EXCLUDED.recommended_action,updated_at=now()""",
            (row["canonical_candidate_id"], run_id, row["candidate_id"], row["candidate_name"],
             row["mailbox_id"], row["email_address"], row["monitoring_status"],
             row["connection_status"], row["last_successful_sync_at"], row["scan_status"],
             row["scan_error"], row["messages_examined"], row["relevant_messages"],
             _json(companies), _json(counts), row["strongest_outcome"], row["strongest_outcome_rank"],
             row["strongest_finding_id"], row["strongest_confidence"], row["strongest_authenticity"],
             row["latest_outcome"], row["latest_outcome_at"], row["system_status"],
             row["system_status_source"], row["status_mismatch"], row["mismatch_detail"],
             row["manual_review_required"], row["conflicting_evidence"], row["suspicious_evidence"],
             row["recommended_action"]),
        )
    return row


# ── Run orchestration ────────────────────────────────────────────────────────

def audit_mailbox(mailbox: dict[str, Any], *, run_id: str, since: datetime | None = None) -> dict[str, Any]:
    """Audit one mailbox. Read-only over mail; writes only audit tables."""
    messages = _messages_for_mailbox(str(mailbox["id"]), since=since)
    findings = [audit_message(message, mailbox) for message in messages]
    gaps = _mailbox_gaps(mailbox, findings)
    _persist_findings(run_id, findings)
    _persist_gaps(run_id, gaps)
    # The rollup always reflects the whole mailbox, even on an incremental run,
    # so a candidate's strongest outcome never regresses because this pass only
    # looked at new mail.
    all_findings = findings if since is None else _stored_findings(str(mailbox["id"]))
    candidate = _persist_candidate(
        run_id, mailbox, all_findings, scan_status="SCANNED", scan_error=None,
        messages_examined=len(messages),
    )
    return {"candidate": candidate, "findings": findings, "gaps": gaps,
            "messages_examined": len(messages)}


def _stored_findings(mailbox_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id,outcome,confidence,received_at,company_name,company_domain,
                      authenticity,manual_review_required,sender_domain
               FROM mail_outcome_audit_findings WHERE mailbox_id=%s""",
            (mailbox_id,),
        )
        return _rows(cur)


def run_audit(*, requested_by: str, candidate_id: str | None = None,
              incremental: bool = False, mode: str = REPORT_ONLY) -> dict[str, Any]:
    """Audit every authorized mailbox and record the results.

    ``mode`` is always REPORT_ONLY here: this function never changes candidate
    status.  Applying an outcome requires ``approve_outcome``.
    """
    ensure_schema()
    run_id = _id()
    scope = candidate_id or "ALL"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_outcome_audit_runs
                 (id,mode,scope,requested_by,status,incremental)
               VALUES (%s,%s,%s,%s,'RUNNING',%s)""",
            (run_id, REPORT_ONLY, scope, requested_by, incremental),
        )

    mailboxes = authorized_mailboxes(candidate_id=candidate_id)
    since = _last_completed_run_at() if incremental else None
    scanned = failed = examined = findings_written = gaps_written = 0
    errors: list[str] = []

    for mailbox in mailboxes:
        try:
            result = audit_mailbox(mailbox, run_id=run_id, since=since)
            scanned += 1
            examined += result["messages_examined"]
            findings_written += len(result["findings"])
            gaps_written += len(result["gaps"])
        except Exception as exc:  # one bad mailbox must not end the audit
            failed += 1
            errors.append(f"{mailbox.get('email_address')}: {type(exc).__name__}: {exc}")
            logger.exception("Outcome audit failed mailbox_id=%s", mailbox.get("id"))
            try:
                _persist_candidate(run_id, mailbox, [], scan_status="FAILED",
                                   scan_error=f"{type(exc).__name__}: {exc}"[:400],
                                   messages_examined=0)
            except Exception:
                logger.exception("Could not record failed mailbox mailbox_id=%s", mailbox.get("id"))

    # Cleanup runs over the whole finding set once the scan is complete, so a
    # duplicate whose twin arrived in a different mailbox pass is still caught.
    cleanup = {}
    try:
        cleanup = recompute_cleanup(run_id=run_id, decided_by=requested_by)
    except Exception:
        logger.exception("Selection-audit cleanup failed run_id=%s", run_id)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_outcome_audit_runs SET status=%s,mailboxes_total=%s,mailboxes_scanned=%s,
                 mailboxes_failed=%s,messages_examined=%s,findings_written=%s,gaps_written=%s,
                 error_message=%s,completed_at=now() WHERE id=%s""",
            # A run that scanned at least one mailbox is a completed run whose
            # per-mailbox failures are recorded; only a total wash is FAILED.
            ("COMPLETED" if scanned or not mailboxes else "FAILED", len(mailboxes), scanned, failed,
             examined, findings_written, gaps_written, ("; ".join(errors)[:2000] or None), run_id),
        )

    store.audit(actor=requested_by, role="admin", action="mail_outcome_audit_run",
                source_id=run_id, new={"scope": scope, "mailboxes": len(mailboxes),
                                       "scanned": scanned, "failed": failed,
                                       "incremental": incremental})
    return {
        "run_id": run_id, "mailboxes_total": len(mailboxes), "mailboxes_scanned": scanned,
        "mailboxes_failed": failed, "messages_examined": examined,
        "findings_written": findings_written, "gaps_written": gaps_written,
        "errors": errors, "mode": REPORT_ONLY, "incremental": incremental,
        "cleanup": cleanup,
    }


def _last_completed_run_at() -> datetime | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT started_at FROM mail_outcome_audit_runs
               WHERE status='COMPLETED' ORDER BY started_at DESC LIMIT 1"""
        )
        row = cur.fetchone()
    return row[0] if row else None


# ── Reporting ────────────────────────────────────────────────────────────────

def _filter_clauses(filters: dict[str, Any], *, alias: str) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    candidate = (filters.get("candidate") or "").strip()
    if candidate:
        clauses.append(
            f"({alias}.canonical_candidate_id=%s OR {alias}.candidate_id=%s"
            f" OR lower({alias}.email_address) LIKE %s"
            + (f" OR lower({alias}.candidate_name) LIKE %s" if alias == "c" else "")
            + ")"
        )
        params.extend([candidate, candidate, f"%{candidate.lower()}%"])
        if alias == "c":
            params.append(f"%{candidate.lower()}%")
    return clauses, params


# ── Mode-scoped reporting ────────────────────────────────────────────────────
#
# Selection and interview-slot results are reported separately. Rather than
# store two rollups per candidate, each mode's view is derived from the same
# findings at read time: the audit does not need re-running to change how its
# results are grouped, and the two totals can never drift apart.

def _findings_for_mode(mode: str, *, include_suppressed: bool = False) -> dict[str, list[dict[str, Any]]]:
    outcomes = sorted(engine.outcomes_for_mode(mode))
    clauses = ["outcome = ANY(%s)"]
    params: list[Any] = [outcomes]
    # Cleanup applies to the selection audit. An interview result excluded
    # there is still a first-class result in the Interview Slot Audit.
    if not include_suppressed and engine.normalize_mode(mode) == engine.MODE_SELECTION:
        clauses.append("COALESCE(suppressed,false)=false")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT id,canonical_candidate_id,outcome,confidence,received_at,company_name,
                       company_domain,sender_domain,authenticity,manual_review_required,
                       pipeline_outcome,pipeline_agreement,subject,suppressed,
                       suppression_reason,suppression_detail,suppressed_at
                FROM mail_outcome_audit_findings
                WHERE {' AND '.join(clauses)}""",
            params,
        )
        rows = _rows(cur)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["canonical_candidate_id"]), []).append(row)
    return grouped


# ── Cleanup ──────────────────────────────────────────────────────────────────

def _cleanup_candidates() -> dict[str, list[dict[str, Any]]]:
    """Every finding, with the fields the suppression rules need."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id,canonical_candidate_id,provider_message_id,provider_thread_id,
                      outcome,confidence,received_at,company_name,company_domain,sender_domain,
                      evidence,content_signature,attachment_fingerprint,rationale,
                      suppressed,suppression_reason
               FROM mail_outcome_audit_findings"""
        )
        rows = _rows(cur)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["canonical_candidate_id"]), []).append(row)
    return grouped


def recompute_cleanup(*, run_id: str | None = None, decided_by: str = "system") -> dict[str, Any]:
    """Re-evaluate which findings the selection audit should skip.

    Nothing is deleted. Each decision is written to the finding and appended to
    the cleanup log with its reason and timestamp, and a finding whose outcome
    later changes is restored automatically.
    """
    ensure_schema()
    grouped = _cleanup_candidates()
    suppressed = restored = changed = 0
    by_reason: dict[str, int] = {}

    with get_connection() as conn, conn.cursor() as cur:
        for canonical, findings in grouped.items():
            decisions = engine.selection_suppressions(findings)
            for finding in findings:
                key = str(finding["id"])
                decision = decisions.get(key)
                was = bool(finding.get("suppressed"))
                previous = finding.get("suppression_reason")

                if decision:
                    by_reason[decision["reason"]] = by_reason.get(decision["reason"], 0) + 1
                    if was and previous == decision["reason"]:
                        continue
                    cur.execute(
                        """UPDATE mail_outcome_audit_findings
                           SET suppressed=true,suppression_reason=%s,suppression_detail=%s,
                               suppression_mode=%s,suppressed_at=now(),updated_at=now()
                           WHERE id=%s""",
                        (decision["reason"], decision["detail"][:1000],
                         engine.MODE_SELECTION, key),
                    )
                    action = "REASON_CHANGED" if was else "SUPPRESSED"
                    cur.execute(
                        """INSERT INTO mail_outcome_audit_cleanup_log
                             (id,finding_id,canonical_candidate_id,run_id,mode,action,
                              reason,detail,previous_reason,decided_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (_id(), key, canonical, run_id, engine.MODE_SELECTION, action,
                         decision["reason"], decision["detail"][:1000], previous, decided_by),
                    )
                    suppressed += 1 if action == "SUPPRESSED" else 0
                    changed += 1 if action == "REASON_CHANGED" else 0
                elif was:
                    cur.execute(
                        """UPDATE mail_outcome_audit_findings
                           SET suppressed=false,suppression_reason=NULL,suppression_detail=NULL,
                               suppression_mode=NULL,suppressed_at=NULL,updated_at=now()
                           WHERE id=%s""",
                        (key,),
                    )
                    cur.execute(
                        """INSERT INTO mail_outcome_audit_cleanup_log
                             (id,finding_id,canonical_candidate_id,run_id,mode,action,
                              reason,detail,previous_reason,decided_by)
                           VALUES (%s,%s,%s,%s,%s,'RESTORED',NULL,%s,%s,%s)""",
                        (_id(), key, canonical, run_id, engine.MODE_SELECTION,
                         "Re-audit no longer matches a cleanup rule.", previous, decided_by),
                    )
                    restored += 1

    # The first three are deltas for this pass; a re-run over an already clean
    # set legitimately reports zero, so the current total is reported too.
    return {"suppressed": suppressed, "restored": restored, "reason_changed": changed,
            "by_reason": by_reason,
            "total_suppressed": sum(by_reason.values())}


def excluded_findings(canonical_candidate_id: str | None = None,
                      *, limit: int = 500) -> list[dict[str, Any]]:
    """Suppressed findings, still carrying their mail and evidence."""
    clauses = ["COALESCE(f.suppressed,false)=true"]
    params: list[Any] = []
    if canonical_candidate_id:
        clauses.append("f.canonical_candidate_id=%s")
        params.append(canonical_candidate_id)
    params.append(max(1, min(2000, int(limit))))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT f.id,f.canonical_candidate_id,f.subject,f.sender_email,f.company_name,
                       f.received_at,f.outcome,f.confidence,f.suppression_reason,
                       f.suppression_detail,f.suppressed_at,c.candidate_name,c.email_address
                FROM mail_outcome_audit_findings f
                LEFT JOIN mail_outcome_audit_candidates c
                  ON c.canonical_candidate_id=f.canonical_candidate_id
                WHERE {' AND '.join(clauses)}
                ORDER BY f.suppressed_at DESC NULLS LAST, f.received_at DESC
                LIMIT %s""",
            params,
        )
        return _rows(cur)


def cleanup_summary() -> dict[str, Any]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT suppression_reason AS reason,count(*) AS total
               FROM mail_outcome_audit_findings
               WHERE COALESCE(suppressed,false)=true GROUP BY 1 ORDER BY 2 DESC"""
        )
        by_reason = _rows(cur)
        cur.execute(
            "SELECT count(*) FROM mail_outcome_audit_findings WHERE COALESCE(suppressed,false)=true"
        )
        total = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT max(created_at) FROM mail_outcome_audit_cleanup_log")
        last = cur.fetchone()[0]
    return {"excluded_total": total, "by_reason": by_reason, "last_cleanup_at": last}


def cleanup_log(canonical_candidate_id: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if canonical_candidate_id:
        clauses.append("canonical_candidate_id=%s")
        params.append(canonical_candidate_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(1000, int(limit))))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT * FROM mail_outcome_audit_cleanup_log {where}
                ORDER BY created_at DESC LIMIT %s""",
            params,
        )
        return _rows(cur)


def _booking_rows_by_candidate() -> dict[str, list[dict[str, Any]]]:
    """Auto-booking outcomes, keyed by the candidate identity the audit uses."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT b.id,b.candidate_id,
                      COALESCE(l.canonical_candidate_id,b.candidate_id) AS canonical_candidate_id,
                      b.classification,b.auto_booked,b.booking_status,b.validation_status,
                      b.duplicate_check_status,b.conflict_check_status,b.failure_code,
                      b.failure_message,b.gmail_message_id,b.created_at
               FROM interview_auto_booking_audit b
               LEFT JOIN candidate_identity_links l ON l.alias_candidate_id=b.candidate_id"""
        )
        rows = _rows(cur)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["booking_outcome"] = engine.booking_outcome(row)
        grouped.setdefault(str(row["canonical_candidate_id"]), []).append(row)
    return grouped


def _gap_counts_by_candidate(mode: str) -> dict[str, int]:
    """Gaps attributable to one mode, plus mailbox-level gaps that affect both."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT canonical_candidate_id,audit_outcome,count(*) AS total
               FROM mail_outcome_audit_gaps GROUP BY 1,2"""
        )
        rows = _rows(cur)
    counts: dict[str, int] = {}
    for row in rows:
        outcome = row.get("audit_outcome")
        owner = engine.mode_for_outcome(str(outcome)) if outcome else None
        # A sync or backlog gap has no outcome and degrades both reports.
        if owner is not None and owner != engine.normalize_mode(mode):
            continue
        key = str(row["canonical_candidate_id"])
        counts[key] = counts.get(key, 0) + int(row["total"])
    return counts


def _base_candidate_rows() -> list[dict[str, Any]]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mail_outcome_audit_candidates")
        return _rows(cur)


def mode_candidate_rows(mode: str) -> list[dict[str, Any]]:
    """One row per candidate, scoped to a single audit mode."""
    mode = engine.normalize_mode(mode)
    base = _base_candidate_rows()
    findings = _findings_for_mode(mode)
    bookings = _booking_rows_by_candidate() if mode == engine.MODE_INTERVIEW else {}
    gaps = _gap_counts_by_candidate(mode)

    result: list[dict[str, Any]] = []
    for row in base:
        key = str(row["canonical_candidate_id"])
        mine = findings.get(key, [])
        counts = engine.outcome_counts(mine)

        if mode == engine.MODE_INTERVIEW:
            for booking in bookings.get(key, []):
                outcome = booking["booking_outcome"]
                counts[outcome] = counts.get(outcome, 0) + 1

        best = engine.strongest(mine)
        # In interview mode a completed booking is the strongest statement the
        # system can make about a slot, ahead of the invitation that caused it.
        booked = [b for b in bookings.get(key, [])
                  if b["booking_outcome"] == engine.BOOKING_AUTO_BOOKED]
        if mode == engine.MODE_INTERVIEW and booked:
            strongest_outcome = engine.BOOKING_AUTO_BOOKED
            strongest_confidence = 100.0
            strongest_finding_id = None
        elif best:
            strongest_outcome = best["outcome"]
            strongest_confidence = float(best.get("confidence") or 0)
            strongest_finding_id = best.get("id")
        elif counts:
            # No ranked outcome, but the mode still has something to report:
            # booking-side evidence in interview mode, or a finding that needs
            # review in selection mode.
            order = (engine.INTERVIEW_MODE_CATEGORIES if mode == engine.MODE_INTERVIEW
                     else engine.SELECTION_MODE_CATEGORIES)
            ranked = [category for category in order if counts.get(category)]
            strongest_outcome = ranked[0] if ranked else engine.NOT_RELEVANT
            strongest_confidence = 100.0 if mode == engine.MODE_INTERVIEW else 0.0
            strongest_finding_id = None
        else:
            strongest_outcome = engine.NOT_RELEVANT
            strongest_confidence = 0.0
            strongest_finding_id = None

        relevant = [f for f in mine if f["outcome"] in engine.MEANINGFUL_OUTCOMES]
        conflicts = engine.detect_conflicts(mine) if mode == engine.MODE_SELECTION else []
        latest = max(mine, key=lambda f: str(f.get("received_at") or ""), default=None)
        relevant_count = len(relevant)
        if mode == engine.MODE_INTERVIEW:
            relevant_count += len(bookings.get(key, []))

        entry = dict(row)
        entry.update({
            "mode": mode,
            "outcome_counts": counts,
            "relevant_messages": relevant_count,
            "messages_examined": row.get("messages_examined"),
            "strongest_outcome": strongest_outcome,
            "strongest_outcome_rank": engine.OUTCOME_RANK.get(strongest_outcome, 0),
            "strongest_confidence": strongest_confidence,
            "strongest_finding_id": strongest_finding_id,
            "strongest_authenticity": (best or {}).get("authenticity"),
            "latest_outcome": (latest or {}).get("outcome"),
            "latest_outcome_at": (latest or {}).get("received_at"),
            "companies": sorted({
                str(f.get("company_name") or f.get("sender_domain") or "").strip()
                for f in relevant if (f.get("company_name") or f.get("sender_domain"))
            }),
            "manual_review_required": any(f["manual_review_required"] for f in mine) or bool(conflicts),
            "conflicting_evidence": bool(conflicts),
            "suspicious_evidence": any(
                f["authenticity"] == engine.AUTHENTICITY_SUSPICIOUS for f in relevant
            ),
            "pipeline_gaps": gaps.get(key, 0),
        })

        # A status mismatch is a statement about hiring status, which only the
        # selection audit is entitled to make. Interview-slot results say
        # nothing about whether a candidate was selected.
        if mode == engine.MODE_SELECTION:
            expected = _AUDIT_TO_CANDIDATE_STATUS.get(strongest_outcome)
            system_status = row.get("system_status")
            mismatch, detail = _status_mismatch(expected, system_status, strongest_outcome)
            entry["status_mismatch"] = mismatch
            entry["mismatch_detail"] = detail
            entry["recommended_action"] = _recommendation(
                conflicts, entry["suspicious_evidence"], mismatch, expected,
                entry["manual_review_required"], relevant,
            )
        else:
            entry["status_mismatch"] = False
            entry["mismatch_detail"] = None
            entry["recommended_action"] = _interview_recommendation(counts)
        result.append(entry)
    return result


def _status_mismatch(expected: str | None, system_status: str | None,
                     strongest_outcome: str) -> tuple[bool, str | None]:
    if expected and system_status and expected != system_status:
        return True, (f"Mail evidence supports '{expected}' ({strongest_outcome}); "
                      f"TeleAutomation shows '{system_status}'.")
    if expected and not system_status:
        return True, (f"Mail evidence supports '{expected}' ({strongest_outcome}); "
                      "TeleAutomation has no detected job status for this candidate.")
    if not expected and system_status and system_status not in {"Profile Active", "Needs Review"}:
        return True, (f"TeleAutomation shows '{system_status}' but the audit found no "
                      "supporting selection evidence.")
    return False, None


def _recommendation(conflicts, suspicious, mismatch, expected, manual, relevant) -> str:
    if conflicts:
        return "Human review: conflicting outcomes for the same company."
    if suspicious:
        return "Human review: sender authenticity concerns on a material outcome."
    if mismatch:
        return (f"Review and, if correct, approve the status update to '{expected}'."
                if expected else "Review why the system status has no mail evidence.")
    if manual:
        return "Human review: incomplete evidence on one or more messages."
    if relevant:
        return "No action; system status matches the mail evidence."
    return "No selection evidence found in this mailbox."


def _interview_recommendation(counts: dict[str, int]) -> str:
    if counts.get(engine.BOOKING_SLOT_CONFLICT):
        return "Resolve the slot conflict before the interview date."
    if counts.get(engine.BOOKING_MISSING_SCHEDULE):
        return "Interview mail carries no usable date or time; confirm with the recruiter."
    if counts.get(engine.BOOKING_BLOCKED):
        return "Automatic booking was blocked; review and book manually if still valid."
    if counts.get(engine.INVITE_UNPROCESSED):
        return "An invitation was never processed into a booking; check the pipeline gap."
    if counts.get(engine.BOOKING_DUPLICATE_IGNORED):
        return "Duplicate invitation ignored; no action unless the slot changed."
    if counts.get(engine.BOOKING_AUTO_BOOKED):
        return "No action; the interview slot was booked automatically."
    if counts:
        return "Interview mail present; no booking action outstanding."
    return "No interview activity found in this mailbox."


def candidate_report(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Mode-scoped candidate rows with the report filters applied."""
    filters = filters or {}
    mode = engine.normalize_mode(filters.get("mode"))
    rows = mode_candidate_rows(mode)

    candidate = str(filters.get("candidate") or "").strip().lower()
    if candidate:
        rows = [
            row for row in rows
            if candidate in str(row.get("canonical_candidate_id") or "").lower()
            or candidate in str(row.get("candidate_id") or "").lower()
            or candidate in str(row.get("email_address") or "").lower()
            or candidate in str(row.get("candidate_name") or "").lower()
        ]

    outcome = str(filters.get("outcome") or "").strip().upper()
    if outcome and outcome != "ALL":
        rows = [row for row in rows if row.get("strongest_outcome") == outcome]

    company = str(filters.get("company") or "").strip().lower()
    if company:
        rows = [row for row in rows
                if any(company in str(item).lower() for item in row.get("companies") or [])]

    if str(filters.get("manual_review") or "").lower() in {"1", "true", "yes"}:
        rows = [row for row in rows if row.get("manual_review_required")]
    if str(filters.get("mismatch") or "").lower() in {"1", "true", "yes"}:
        rows = [row for row in rows if row.get("status_mismatch")]

    authenticity = str(filters.get("authenticity") or "").strip().upper()
    if authenticity and authenticity != "ALL":
        rows = [row for row in rows if row.get("strongest_authenticity") == authenticity]

    sync_status = str(filters.get("sync_status") or "").strip().upper()
    if sync_status and sync_status != "ALL":
        if sync_status == "MONITORING_ACTIVE":
            rows = [row for row in rows if row.get("monitoring_status") == "MONITORING_ACTIVE"]
        elif sync_status == "FAILED":
            rows = [row for row in rows
                    if row.get("scan_status") == "FAILED" or row.get("connection_status") == "ERROR"]
        else:
            rows = [row for row in rows if row.get("connection_status") == sync_status]

    minimum = filters.get("min_confidence")
    if minimum not in (None, "", "all"):
        rows = [row for row in rows if float(row.get("strongest_confidence") or 0) >= float(minimum)]

    rows.sort(key=lambda row: (
        -int(row.get("strongest_outcome_rank") or 0),
        str(row.get("candidate_name") or ""),
    ))
    return rows


def _legacy_candidate_report(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses: list[str] = []
    params: list[Any] = []

    candidate = str(filters.get("candidate") or "").strip()
    if candidate:
        clauses.append(
            "(c.canonical_candidate_id=%s OR c.candidate_id=%s"
            " OR lower(COALESCE(c.email_address,'')) LIKE %s"
            " OR lower(COALESCE(c.candidate_name,'')) LIKE %s)"
        )
        params.extend([candidate, candidate, f"%{candidate.lower()}%", f"%{candidate.lower()}%"])

    outcome = str(filters.get("outcome") or "").strip().upper()
    if outcome and outcome != "ALL":
        clauses.append("c.strongest_outcome=%s")
        params.append(outcome)

    company = str(filters.get("company") or "").strip()
    if company:
        clauses.append("EXISTS (SELECT 1 FROM jsonb_array_elements_text(c.companies) x "
                       "WHERE lower(x) LIKE %s)")
        params.append(f"%{company.lower()}%")

    if str(filters.get("manual_review") or "").lower() in {"1", "true", "yes"}:
        clauses.append("c.manual_review_required=true")
    if str(filters.get("mismatch") or "").lower() in {"1", "true", "yes"}:
        clauses.append("c.status_mismatch=true")

    authenticity = str(filters.get("authenticity") or "").strip().upper()
    if authenticity and authenticity != "ALL":
        clauses.append("c.strongest_authenticity=%s")
        params.append(authenticity)

    sync_status = str(filters.get("sync_status") or "").strip().upper()
    if sync_status and sync_status != "ALL":
        if sync_status == "MONITORING_ACTIVE":
            clauses.append("c.monitoring_status='MONITORING_ACTIVE'")
        elif sync_status == "FAILED":
            clauses.append("(c.scan_status='FAILED' OR c.connection_status='ERROR')")
        else:
            clauses.append("c.connection_status=%s")
            params.append(sync_status)

    minimum = filters.get("min_confidence")
    if minimum not in (None, "", "all"):
        clauses.append("c.strongest_confidence >= %s")
        params.append(float(minimum))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT c.* FROM mail_outcome_audit_candidates c {where}
                ORDER BY c.strongest_outcome_rank DESC, c.updated_at DESC""",
            params,
        )
        rows = _rows(cur)
    return rows


def candidate_findings(canonical_candidate_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses = ["f.canonical_candidate_id=%s"]
    params: list[Any] = [canonical_candidate_id]

    # The drawer belongs to whichever audit opened it, so selection evidence
    # never appears inside an interview-slot review and vice versa.
    mode = filters.get("mode")
    if mode and str(mode).upper() != "ALL":
        clauses.append("f.outcome = ANY(%s)")
        params.append(sorted(engine.outcomes_for_mode(mode)))
        # Cleaned-up findings are listed separately, not mixed into the
        # evidence the counted outcome rests on.
        if (engine.normalize_mode(mode) == engine.MODE_SELECTION
                and str(filters.get("include_excluded") or "").lower() not in {"1", "true", "yes"}):
            clauses.append("COALESCE(f.suppressed,false)=false")

    if str(filters.get("relevant_only") or "").lower() in {"1", "true", "yes"}:
        clauses.append("f.outcome <> 'NOT_RELEVANT'")
    date_from = filters.get("date_from")
    if date_from:
        clauses.append("f.received_at >= %s")
        params.append(date_from)
    date_to = filters.get("date_to")
    if date_to:
        clauses.append("f.received_at <= %s")
        params.append(date_to)
    outcome = str(filters.get("outcome") or "").strip().upper()
    if outcome and outcome != "ALL":
        clauses.append("f.outcome=%s")
        params.append(outcome)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT f.* FROM mail_outcome_audit_findings f
                WHERE {' AND '.join(clauses)}
                ORDER BY f.received_at NULLS LAST, f.created_at""",
            params,
        )
        return _rows(cur)


# Tiles per mode. Each label is a category the operator asked to see, and the
# two lists share no category, so a number can never appear in both reports.
SELECTION_TILES = (
    ("candidates_verified_offer_letters", engine.VERIFIED_OFFER_LETTER),
    ("candidates_final_selection", engine.FINAL_SELECTION),
    ("candidates_offer_indication", engine.OFFER_INDICATION),
    ("candidates_joining_confirmed", engine.JOINING_CONFIRMED),
    ("candidates_background_verification", engine.BACKGROUND_VERIFICATION),
    ("candidates_shortlisted", engine.SHORTLISTED),
    ("candidates_next_round", engine.NEXT_ROUND),
    ("candidates_rejected", engine.REJECTED),
    ("candidates_manual_review_outcome", engine.MANUAL_REVIEW_REQUIRED),
)
INTERVIEW_TILES = (
    ("candidates_with_interview_invites", engine.INTERVIEW_INVITE),
    ("candidates_auto_booked", engine.BOOKING_AUTO_BOOKED),
    ("candidates_interview_rescheduled", engine.INTERVIEW_RESCHEDULED),
    ("candidates_interview_cancelled", engine.INTERVIEW_CANCELLED),
    ("candidates_booking_blocked", engine.BOOKING_BLOCKED),
    ("candidates_duplicate_booking_ignored", engine.BOOKING_DUPLICATE_IGNORED),
    ("candidates_slot_conflict", engine.BOOKING_SLOT_CONFLICT),
    ("candidates_missing_date_or_time", engine.BOOKING_MISSING_SCHEDULE),
    ("candidates_missed_invites", engine.INVITE_UNPROCESSED),
    ("candidates_historical_not_booked", engine.BOOKING_HISTORICAL_SKIPPED),
)


def system_summary(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """The administrator report for one audit mode."""
    filters = filters or {}
    mode = engine.normalize_mode(filters.get("mode"))
    rows = candidate_report(filters)
    gaps = gap_totals(mode)

    # Counted over every candidate that has the category at all, not only where
    # it is the strongest outcome: an operator asking "who has a blocked
    # booking" wants all of them, not just those with nothing stronger.
    def with_category(category: str) -> int:
        return sum(1 for row in rows if (row.get("outcome_counts") or {}).get(category))

    tiles = SELECTION_TILES if mode == engine.MODE_SELECTION else INTERVIEW_TILES
    summary = {key: with_category(category) for key, category in tiles}

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mail_outcome_audit_runs ORDER BY started_at DESC LIMIT 1")
        run = _rows(cur)
        cur.execute(
            """SELECT count(*) FROM mail_outcome_audit_findings
               WHERE authenticity='SUSPICIOUS' AND outcome = ANY(%s)""",
            (sorted(engine.outcomes_for_mode(mode)),),
        )
        suspicious_findings = int(cur.fetchone()[0] or 0)

    missed = sum(int(item["total"]) for item in gaps
                 if item["gap_type"] in {GAP_MISSING_EVENT, GAP_MISCLASSIFIED, GAP_DEDUP_SUPPRESSED})
    queue_failures = sum(int(item["total"]) for item in gaps
                         if item["gap_type"] in {GAP_AI_QUEUE_FAILURE, GAP_SYNC_FAILURE,
                                                 GAP_SYNC_INCOMPLETE})

    summary.update({
        "mode": mode,
        "generated_at": now(),
        "latest_run": run[0] if run else None,
        "total_connected_mailboxes": len(rows),
        "mailboxes_scanned": sum(1 for row in rows if row.get("scan_status") == "SCANNED"),
        "mailboxes_failed": sum(1 for row in rows if row.get("scan_status") == "FAILED"),
        "mailboxes_inaccessible": sum(1 for row in rows if row.get("connection_status") == "ERROR"),
        "candidates_no_outcome": sum(
            1 for row in rows if row.get("strongest_outcome") == engine.NOT_RELEVANT),
        "candidates_manual_review": sum(1 for row in rows if row.get("manual_review_required")),
        "candidates_suspicious_evidence": sum(1 for row in rows if row.get("suspicious_evidence")),
        "suspicious_findings": suspicious_findings,
        "emails_missed_or_misclassified": missed,
        "sync_or_queue_failures": queue_failures,
        "pipeline_gaps_total": sum(int(item["total"]) for item in gaps),
        "gaps_by_type": gaps,
    })
    if mode == engine.MODE_SELECTION:
        summary["candidates_status_mismatch"] = sum(
            1 for row in rows if row.get("status_mismatch"))
        summary["candidates_conflicting_evidence"] = sum(
            1 for row in rows if row.get("conflicting_evidence"))
        cleanup = cleanup_summary()
        summary["excluded_findings"] = cleanup["excluded_total"]
        summary["excluded_by_reason"] = cleanup["by_reason"]
        summary["last_cleanup_at"] = cleanup["last_cleanup_at"]
    return summary


def gap_totals(mode: str) -> list[dict[str, Any]]:
    """Gap counts scoped to one mode, mailbox-level gaps counted in both."""
    mode = engine.normalize_mode(mode)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT gap_type,severity,audit_outcome,count(*) AS total
               FROM mail_outcome_audit_gaps GROUP BY 1,2,3"""
        )
        rows = _rows(cur)
    totals: dict[tuple[str, str], int] = {}
    for row in rows:
        outcome = row.get("audit_outcome")
        owner = engine.mode_for_outcome(str(outcome)) if outcome else None
        if owner is not None and owner != mode:
            continue
        key = (str(row["gap_type"]), str(row["severity"]))
        totals[key] = totals.get(key, 0) + int(row["total"])
    return [
        {"gap_type": gap_type, "severity": severity, "total": total}
        for (gap_type, severity), total in sorted(totals.items(), key=lambda item: -item[1])
    ]


def list_gaps(*, gap_type: str | None = None, candidate_id: str | None = None,
              mode: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if mode and str(mode).upper() != "ALL":
        scoped = engine.normalize_mode(mode)
        owned = sorted(
            outcome for outcome in
            (set(engine.OUTCOMES) | set(engine.BOOKING_OUTCOMES))
            if engine.mode_for_outcome(outcome) == scoped
        )
        # Mailbox-level gaps carry no outcome and degrade both reports.
        clauses.append("(g.audit_outcome IS NULL OR g.audit_outcome = ANY(%s))")
        params.append(owned)
    if gap_type and gap_type != "ALL":
        clauses.append("g.gap_type=%s")
        params.append(gap_type)
    if candidate_id:
        clauses.append("g.canonical_candidate_id=%s")
        params.append(candidate_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(1000, int(limit))))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT g.*,c.candidate_name,c.email_address
                FROM mail_outcome_audit_gaps g
                LEFT JOIN mail_outcome_audit_candidates c
                  ON c.canonical_candidate_id=g.canonical_candidate_id
                {where} ORDER BY
                  CASE g.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                  g.created_at DESC LIMIT %s""",
            params,
        )
        return _rows(cur)


# ── Administrator approval ───────────────────────────────────────────────────

def approve_outcome(finding_id: str, *, decision: str, approved_by: str,
                    notes: str = "", force: bool = False) -> dict[str, Any]:
    """Apply an audited outcome to candidate status, on explicit approval only.

    This is the single place where the audit is allowed to change a candidate
    record.  A rejected decision is recorded and changes nothing.
    """
    decision = str(decision or "").strip().upper()
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("decision must be APPROVED or REJECTED")

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mail_outcome_audit_findings WHERE id=%s", (finding_id,))
        rows = _rows(cur)
    if not rows:
        raise LookupError("Audit finding not found")
    finding = rows[0]

    canonical = str(finding["canonical_candidate_id"])
    outcome = str(finding["outcome"])
    previous_status, _ = _system_status(canonical)
    target_status = _AUDIT_TO_CANDIDATE_STATUS.get(outcome)

    approval_id = _id()
    applied = False
    applied_status = None
    error = None

    # The gate is enforced here, not only in the UI: a weak or portal-sourced
    # finding must not become a candidate status through a direct API call.
    eligibility = engine.approval_eligibility(finding)
    if decision == "APPROVED" and not eligibility["eligible"] and not force:
        raise PermissionError(
            eligibility["message"] + " " + " ".join(eligibility["blockers"])
        )

    if decision == "APPROVED":
        if not target_status:
            error = f"Outcome {outcome} does not map to a candidate status and cannot be applied."
        else:
            try:
                _apply_status(canonical, target_status, finding, approved_by, notes)
                applied = True
                applied_status = target_status
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:400]
                logger.exception("Approved audit outcome could not be applied finding_id=%s", finding_id)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_outcome_audit_approvals
                 (id,finding_id,canonical_candidate_id,requested_outcome,previous_system_status,
                  applied_system_status,decision,approved_by,notes,applied,applied_at,error_message)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (approval_id, finding_id, canonical, outcome, previous_status, applied_status,
             decision, approved_by, notes, applied, now() if applied else None, error),
        )

    store.audit(actor=approved_by, role="admin", action="mail_outcome_audit_approval",
                candidate_id=canonical, source_id=finding_id,
                previous={"status": previous_status},
                new={"decision": decision, "outcome": outcome, "applied": applied,
                     "status": applied_status, "error": error})

    if error and decision == "APPROVED":
        raise RuntimeError(error)
    return {"approval_id": approval_id, "decision": decision, "applied": applied,
            "previous_status": previous_status, "status": applied_status,
            "outcome": outcome, "candidate_id": canonical}


def _apply_status(canonical: str, status: str, finding: dict[str, Any],
                  approved_by: str, notes: str) -> None:
    rank = store._STATUS_RANK.get(status, 0)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO candidate_job_status
                 (candidate_id,status,status_rank,source,source_id,gmail_message_id,
                  classification,confidence,updated_at)
               VALUES (%s,%s,%s,'outcome_audit',%s,%s,%s,%s,now())
               ON CONFLICT (candidate_id) DO UPDATE SET
                 status=EXCLUDED.status,status_rank=EXCLUDED.status_rank,
                 source=EXCLUDED.source,source_id=EXCLUDED.source_id,
                 gmail_message_id=EXCLUDED.gmail_message_id,
                 classification=EXCLUDED.classification,confidence=EXCLUDED.confidence,
                 updated_at=now()""",
            (canonical, status, rank, finding["id"], finding.get("provider_message_id"),
             finding.get("outcome"), float(finding.get("confidence") or 0) / 100.0),
        )
        cur.execute(
            """INSERT INTO candidate_status_history
                 (id,candidate_id,previous_detected_status,new_detected_status,confirmed_status,
                  source_type,source_id,confidence,reviewed_by,reviewed_at,review_notes)
               VALUES (%s,%s,NULL,%s,%s,'MAIL_OUTCOME_AUDIT',%s,%s,%s,now(),%s)""",
            (_id(), canonical, status, status, finding["id"],
             float(finding.get("confidence") or 0) / 100.0, approved_by,
             notes or f"Approved from mail outcome audit ({finding.get('outcome')})."),
        )


def list_approvals(*, canonical_candidate_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if canonical_candidate_id:
        clauses.append("canonical_candidate_id=%s")
        params.append(canonical_candidate_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(500, int(limit))))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM mail_outcome_audit_approvals {where} ORDER BY created_at DESC LIMIT %s",
            params,
        )
        return _rows(cur)


def application_timeline(canonical_candidate_id: str,
                         mode: str = engine.MODE_SELECTION) -> list[dict[str, Any]]:
    """One lifecycle per company and role, never merged across companies.

    A later rejection from company B says nothing about an offer from company
    A, so each application carries its own latest verified state and its own
    recommendation.
    """
    mode = engine.normalize_mode(mode)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id,outcome,confidence,received_at,subject,sender_email,sender_domain,
                      company_name,company_domain,job_title,authenticity,evidence_strength,
                      source_type,application_key,manual_review_required,rationale,
                      suppressed,suppression_reason,provider_thread_id
               FROM mail_outcome_audit_findings
               WHERE canonical_candidate_id=%s AND outcome = ANY(%s)
                 AND COALESCE(suppressed,false)=false
               ORDER BY received_at NULLS LAST""",
            (canonical_candidate_id, sorted(engine.outcomes_for_mode(mode))),
        )
        rows = _rows(cur)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get("application_key") or engine.application_key(row)
        grouped.setdefault(str(key), []).append(row)

    applications: list[dict[str, Any]] = []
    for key, entries in grouped.items():
        entries.sort(key=lambda item: str(item.get("received_at") or ""))
        latest = entries[-1]
        best = engine.strongest(entries) or latest
        # Only messages in this application can conflict with each other.
        rank = engine.OUTCOME_RANK.get(str(best.get("outcome")), 0)
        later_conflict = any(
            str(item.get("received_at") or "") > str(best.get("received_at") or "")
            and engine.OUTCOME_RANK.get(str(item.get("outcome")), 0) != rank
            and (str(item.get("outcome")) == engine.REJECTED)
            != (str(best.get("outcome")) == engine.REJECTED)
            for item in entries
        )
        eligibility = engine.approval_eligibility(best, later_conflict=later_conflict)

        applications.append({
            "application_key": key,
            "company": (best.get("company_name") or best.get("company_domain")
                        or best.get("sender_domain") or "Unidentified company"),
            "company_domain": best.get("company_domain"),
            "role": best.get("job_title") or "Role not stated",
            "messages": entries,
            "first_seen": entries[0].get("received_at"),
            "latest_message_at": latest.get("received_at"),
            "latest_verified_state": best.get("outcome"),
            "confidence": best.get("confidence"),
            "authenticity": best.get("authenticity"),
            "evidence_strength": best.get("evidence_strength") or engine.STRENGTH_WEAK,
            "source_type": best.get("source_type"),
            "strongest_finding_id": best.get("id"),
            "later_conflict": later_conflict,
            "approval": eligibility,
            "recommended_action": (
                f"Approve the status update for {best.get('company_name') or key}."
                if eligibility["eligible"] else eligibility["message"]
            ),
        })

    applications.sort(key=lambda item: (
        -engine.OUTCOME_RANK.get(str(item["latest_verified_state"]), 0),
        str(item["company"]),
    ))
    return applications


def candidate_bookings(canonical_candidate_id: str) -> list[dict[str, Any]]:
    """Auto-booking outcomes for one candidate, for the interview-mode drawer."""
    return _booking_rows_by_candidate().get(str(canonical_candidate_id), [])


def export_csv(filters: dict[str, Any] | None = None) -> str:
    """Comma-separated export of the current mode's candidate report.

    The export carries the same rows the screen shows, so a selection export
    can never contain interview-slot totals.
    """
    import csv
    import io

    filters = filters or {}
    mode = engine.normalize_mode(filters.get("mode"))
    rows = candidate_report(filters)
    categories = (engine.SELECTION_MODE_CATEGORIES if mode == engine.MODE_SELECTION
                  else engine.INTERVIEW_MODE_CATEGORIES)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    header = [
        "audit_mode", "candidate_name", "candidate_id", "gmail",
        "monitoring_status", "connection_status", "last_successful_sync_at",
        "messages_examined", "relevant_messages", "companies",
        "strongest_outcome", "confidence", "authenticity",
        "manual_review_required", "suspicious_evidence", "pipeline_gaps",
    ]
    if mode == engine.MODE_SELECTION:
        header += ["system_status", "status_mismatch", "mismatch_detail", "conflicting_evidence"]
    header += ["recommended_action"] + [category.lower() for category in categories]
    writer.writerow(header)

    for row in rows:
        counts = row.get("outcome_counts") or {}
        line = [
            mode, row.get("candidate_name") or "", row.get("canonical_candidate_id") or "",
            row.get("email_address") or "", row.get("monitoring_status") or "",
            row.get("connection_status") or "", row.get("last_successful_sync_at") or "",
            row.get("messages_examined") or 0, row.get("relevant_messages") or 0,
            "; ".join(row.get("companies") or []),
            row.get("strongest_outcome") or "", round(float(row.get("strongest_confidence") or 0)),
            row.get("strongest_authenticity") or "",
            "yes" if row.get("manual_review_required") else "no",
            "yes" if row.get("suspicious_evidence") else "no",
            row.get("pipeline_gaps") or 0,
        ]
        if mode == engine.MODE_SELECTION:
            line += [
                row.get("system_status") or "",
                "yes" if row.get("status_mismatch") else "no",
                row.get("mismatch_detail") or "",
                "yes" if row.get("conflicting_evidence") else "no",
            ]
        line += [row.get("recommended_action") or ""]
        line += [counts.get(category, 0) for category in categories]
        writer.writerow(line)
    return buffer.getvalue()


def finding_history(finding_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM mail_outcome_audit_finding_history
               WHERE finding_id=%s ORDER BY created_at""",
            (finding_id,),
        )
        return _rows(cur)
