"""Ollama-assisted second opinion for the mail outcome audit.

This module is deliberately isolated from the interview auto-booking feature,
which is working correctly and must not be affected:

* it never imports the booking module and never calls a booking function
* it has its own prompt, its own schema, its own queue and its own cache
* it writes only to its own tables, and only ever to advise a human
* it defers entirely to live interview processing for Ollama capacity

Its output cannot create, move or cancel a booking, cannot change candidate
status, and cannot modify Gmail. The strongest thing it can do is attach a
second opinion to an audit finding that an administrator then reads.

The feature is gated by AI_MAIL_AUDIT_ENABLED, which is separate from
AI_INTERVIEW_AUTO_BOOKING_ENABLED and never read here as a substitute.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from core.db.connection import get_connection, use_postgres
from core import recruitment_mail_audit as engine

logger = logging.getLogger("teleautomation.recruitment_audit_ai")

FEATURE_FLAG = "AI_MAIL_AUDIT_ENABLED"

QUEUE_PENDING = "PENDING"
QUEUE_RUNNING = "RUNNING"
QUEUE_DONE = "COMPLETED"
QUEUE_FAILED = "FAILED"
QUEUE_DEFERRED = "DEFERRED"


AUTO_PROCESSING_FLAG = "AI_MAIL_AUDIT_AUTO_PROCESSING_ENABLED"


def enabled() -> bool:
    """The audit's own switch. Never falls back to the booking flag."""
    return os.getenv(FEATURE_FLAG, "false").strip().lower() == "true"


def auto_processing_enabled() -> bool:
    """Whether the worker may drain the audit queue on its own.

    Separate from FEATURE_FLAG so the audit can stay available for manual runs
    while unattended processing is paused. Neither flag has anything to do with
    interview auto-booking.
    """
    return os.getenv(AUTO_PROCESSING_FLAG, "false").strip().lower() == "true"


# ── Confidence ───────────────────────────────────────────────────────────────

def normalize_confidence(value: Any) -> float | None:
    """Return a 0-100 confidence, or None when the model gave nothing usable.

    The first production batch returned 1.0, 0.95, 95.0 and 100.0 for the same
    field. A bare 1.0 is ambiguous — it is read as 100% because the schema asks
    for 0-100 and no model has ever meant "1% confident" by it.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number < 0:
        return None
    if number <= 1.0:
        return round(number * 100.0, 2)
    if number <= 100.0:
        return round(number, 2)
    return None


# ── Agreement ────────────────────────────────────────────────────────────────

def derive_agreement(deterministic: Any, pipeline: Any, ollama: Any) -> str:
    """Compare outcomes. The model's own `agrees` field is never consulted.

    In the first batch one review reported agrees=false while giving the same
    outcome, and another reported agrees=true while giving a different one.
    """
    rules = str(deterministic or "").strip().upper()
    live = str(pipeline or "").strip().upper()
    model = str(ollama or "").strip().upper()
    if not model:
        return "NO_AI_RESULT"
    if model == rules:
        return "AGREES_WITH_RULES"
    if live and model == live:
        return "AGREES_WITH_PIPELINE"
    return "DISAGREES"


def agreement_requires_review(agreement: str) -> bool:
    """Any disagreement between the deterministic reading and the model is a
    prompt for a human, never an automatic correction."""
    return str(agreement or "").upper() not in {"AGREES_WITH_RULES"}


def _id() -> str:
    return str(uuid.uuid4())


def ensure_schema() -> None:
    if not use_postgres():
        return
    migration = (Path(__file__).with_name("migrations")
                 / "022_recruitment_mail_audit_ai.sql")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(migration.read_text(encoding="utf-8"))


def _rows(cur) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


# ── Capacity deference ───────────────────────────────────────────────────────
#
# Live interview processing always wins. The audit does not queue behind it,
# compete with it, or retry into it: when live work exists the audit simply
# does not start, and its jobs stay PENDING until the pipeline is idle.

def live_pipeline_busy() -> dict[str, Any]:
    """Report whether the live mail and booking pipeline has work in flight."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM mailbox_sync_jobs
               WHERE status IN ('QUEUED','RUNNING')"""
        )
        sync_jobs = int(cur.fetchone()[0] or 0)
        # Only work the live worker can actually pick up right now counts as
        # busy. A message parked in exponential backoff until this evening is
        # not competing for the node, and treating it as live work would
        # deadlock the audit for as long as any message is stuck retrying.
        cur.execute(
            """SELECT count(*) FROM mailbox_messages
               WHERE processing_status IN ('AI_RETRY_PENDING','AI_PENDING')
                 AND (ai_retry_after IS NULL OR ai_retry_after <= now())"""
        )
        ai_backlog = int(cur.fetchone()[0] or 0)
        cur.execute(
            """SELECT count(*) FROM mailbox_messages
               WHERE processing_status IN ('AI_RETRY_PENDING','AI_PENDING')
                 AND ai_retry_after > now()"""
        )
        ai_deferred = int(cur.fetchone()[0] or 0)
        cur.execute(
            """SELECT count(*) FROM gmail_message_ingestion_queue
               WHERE status IN ('QUEUED','RUNNING')"""
        )
        ingestion = int(cur.fetchone()[0] or 0)
    busy = bool(sync_jobs or ai_backlog or ingestion)
    return {
        "busy": busy, "sync_jobs": sync_jobs,
        "ai_backlog": ai_backlog, "ingestion": ingestion,
        # Reported for visibility, deliberately not part of `busy`.
        "ai_in_backoff": ai_deferred,
    }


def may_run() -> dict[str, Any]:
    """Whether an audit inference may start right now.

    Every condition is a reason to yield to the booking pipeline rather than a
    reason to wait in its queue.
    """
    if not enabled():
        return {"allowed": False, "reason": f"{FEATURE_FLAG} is not enabled."}
    live = live_pipeline_busy()
    if live["busy"]:
        return {
            "allowed": False,
            "reason": (
                f"Live interview processing is active "
                f"(sync jobs {live['sync_jobs']}, AI backlog {live['ai_backlog']}, "
                f"ingestion {live['ingestion']}); the audit yields."
            ),
            "live": live,
        }
    try:
        from core.ai_gateway import health
        status = health(timeout=5)
    except Exception as exc:
        return {"allowed": False, "reason": f"Ollama health unavailable: {exc}"}
    if not status.get("endpoint_reachable") or not status.get("model_available"):
        return {"allowed": False,
                "reason": "Ollama is unavailable; audit jobs stay pending."}
    return {"allowed": True, "reason": "", "live": live}


def max_concurrency() -> int:
    """Audit inference is single-file by default and hard-capped low."""
    try:
        value = int(os.getenv("AI_MAIL_AUDIT_CONCURRENCY", "1"))
    except ValueError:
        value = 1
    return max(1, min(2, value))


# ── Prompt and schema, owned by the audit alone ──────────────────────────────

AUDIT_PROMPT_NAME = "recruitment_mail_audit_second_opinion_v1"

AUDIT_SYSTEM_PROMPT = """You are the TeleAutomation mail audit reviewer.

You are given one email that a deterministic rule engine has already
classified. Your only job is to give a second opinion on whether that
classification is supported by the text.

You are a read-only reviewer. You cannot book, reschedule or cancel an
interview, you cannot change a candidate's status, and nothing you return is
executed. An administrator reads your answer and decides.

Judge only what the email itself states:
- a job portal or agency relaying news is not the hiring company confirming it
- a bulk campaign or job advertisement is not a decision about this candidate
- a request for profile details is not an interview round
- boilerplate such as "assume you were not shortlisted if you do not hear from
  us" is a general policy, not this candidate's rejection
- an offer letter is only verified when real offer terms are present
- interview scheduling is not selection
If the text does not clearly support any outcome, say so.

You receive the whole thread, oldest first, and the text extracted from any
attachments. Read all of it before answering, and keep companies separate: a
result from one company says nothing about another.

Every answer must be checkable:
- cited_message_id must be one of the message_id values given to you
- quoted_evidence must be text copied verbatim from a body or an attachment
- cited_attachment must be a filename given to you, or null
Do not invent a message, a quotation, an attachment or a company. If nothing
in the input supports a conclusion, set agrees to reflect that and say why.
"""

AUDIT_SCHEMA = {
    "type": "object",
    "required": ["agrees", "suggested_outcome", "confidence", "reasoning",
                 "is_bulk_campaign", "sender_is_hiring_company",
                 "cited_message_id", "quoted_evidence", "company", "cited_attachment"],
    "properties": {
        "agrees": {"type": "boolean"},
        "suggested_outcome": {"type": "string", "enum": sorted(engine.OUTCOMES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "reasoning": {"type": "string", "maxLength": 800},
        "is_bulk_campaign": {"type": "boolean"},
        "sender_is_hiring_company": {"type": "boolean"},
        # Citations exist so the answer can be checked against the source.
        # A message id or quote that is not in the input is a fabrication and
        # is caught by verify_review before the result is trusted.
        "cited_message_id": {"type": "string", "maxLength": 64},
        "quoted_evidence": {"type": "string", "maxLength": 400},
        "cited_attachment": {"type": ["string", "null"], "maxLength": 200},
        "company": {"type": ["string", "null"], "maxLength": 200},
    },
    "additionalProperties": False,
}


def cache_key(finding: dict[str, Any]) -> str:
    """Audit's own cache namespace; shares nothing with booking or detection."""
    payload = "|".join([
        AUDIT_PROMPT_NAME,
        str(finding.get("provider_message_id") or ""),
        str(finding.get("outcome") or ""),
        str(finding.get("content_signature") or ""),
    ])
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _prompt_payload(finding: dict[str, Any], body: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({
            "message_under_review": finding.get("provider_message_id"),
            "rule_engine_outcome": finding.get("outcome"),
            "rule_engine_rationale": finding.get("rationale"),
            "live_pipeline_outcome": finding.get("pipeline_outcome"),
            "subject": finding.get("subject"),
            "sender_email": finding.get("sender_email"),
            "sender_domain": finding.get("sender_domain"),
            "stated_company": finding.get("company_name"),
            "company_domain": finding.get("company_domain"),
            "role": finding.get("job_title"),
            "application_key": finding.get("application_key"),
            "received_at": str(finding.get("received_at") or ""),
            # The full conversation and the real extracted attachment text.
            "thread": finding.get("thread") or [],
            "attachments": finding.get("attachments") or [],
        }, default=str)},
    ]


# ── Queue ────────────────────────────────────────────────────────────────────

def enqueue(finding_ids: list[str], *, requested_by: str = "system") -> int:
    """Queue findings for a second opinion. Idempotent per finding."""
    if not finding_ids:
        return 0
    ensure_schema()
    queued = 0
    with get_connection() as conn, conn.cursor() as cur:
        for finding_id in finding_ids:
            cur.execute(
                """INSERT INTO mail_audit_ai_queue
                     (id,finding_id,status,requested_by)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (finding_id) DO NOTHING""",
                (_id(), finding_id, QUEUE_PENDING, requested_by),
            )
            queued += cur.rowcount or 0
    return queued


def claim(limit: int = 1) -> list[dict[str, Any]]:
    """Take pending audit jobs. Touches no queue but the audit's own."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_audit_ai_queue SET status=%s,started_at=now(),
                   attempts=attempts+1,updated_at=now()
               WHERE id IN (
                 SELECT id FROM mail_audit_ai_queue
                 WHERE status=%s AND (retry_after IS NULL OR retry_after<=now())
                 ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING *""",
            (QUEUE_RUNNING, QUEUE_PENDING, max(1, min(5, int(limit)))),
        )
        return _rows(cur)


def _finish(job_id: str, status: str, *, error: str | None = None,
            delay_minutes: int = 15) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        if status == QUEUE_FAILED:
            cur.execute(
                """UPDATE mail_audit_ai_queue
                   SET status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'PENDING' END,
                       last_error=%s,retry_after=now()+(%s||' minutes')::interval,
                       updated_at=now()
                   WHERE id=%s""",
                (str(error or "")[:400], delay_minutes, job_id),
            )
        else:
            cur.execute(
                """UPDATE mail_audit_ai_queue SET status=%s,completed_at=now(),
                       last_error=NULL,updated_at=now() WHERE id=%s""",
                (status, job_id),
            )


def _finding_for_job(finding_id: str) -> dict[str, Any] | None:
    """Load one finding with everything the reviewer must actually read.

    The complete thread and the extracted attachment text travel with the
    prompt, because a second opinion formed from the subject line alone is
    worth no more than the rule it is checking.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.id,f.provider_message_id,f.provider_thread_id,f.outcome,
                      f.rationale,f.subject,f.sender_email,f.sender_domain,
                      f.company_name,f.company_domain,f.job_title,f.received_at,
                      f.content_signature,f.application_key,f.source_type,
                      f.evidence_strength,f.authenticity,f.pipeline_outcome,
                      f.mailbox_id,f.mailbox_message_id,f.canonical_candidate_id,
                      m.body_text
               FROM mail_outcome_audit_findings f
               LEFT JOIN mailbox_messages m ON m.id=f.mailbox_message_id
               WHERE f.id=%s""",
            (finding_id,),
        )
        rows = _rows(cur)
    if not rows:
        return None
    finding = rows[0]

    with get_connection() as conn, conn.cursor() as cur:
        # The whole conversation, oldest first, so chronology is visible.
        cur.execute(
            """SELECT provider_message_id,subject,sender_email,sent_at,body_text,
                      message_direction
               FROM mailbox_messages
               WHERE mailbox_id=%s AND provider_thread_id=%s
               ORDER BY COALESCE(sent_at,created_at)""",
            (finding.get("mailbox_id"), finding.get("provider_thread_id") or ""),
        )
        thread = _rows(cur)
        cur.execute(
            """SELECT a.filename,a.mime_type,a.attachment_type,a.extraction_status,
                      a.checksum,c.extracted_text
               FROM mailbox_attachments a
               LEFT JOIN mailbox_attachment_cache c ON c.checksum=a.checksum
               WHERE a.mailbox_message_id=%s""",
            (finding.get("mailbox_message_id"),),
        )
        attachments = _rows(cur)

    finding["thread"] = [
        {
            "message_id": row.get("provider_message_id"),
            "subject": row.get("subject"),
            "from": row.get("sender_email"),
            "sent_at": str(row.get("sent_at") or ""),
            "direction": row.get("message_direction"),
            "body": (row.get("body_text") or "")[:2500],
        }
        for row in thread
    ] or [{
        "message_id": finding.get("provider_message_id"),
        "subject": finding.get("subject"), "from": finding.get("sender_email"),
        "sent_at": str(finding.get("received_at") or ""), "direction": None,
        "body": (finding.get("body_text") or "")[:2500],
    }]
    finding["attachments"] = [
        {
            "filename": row.get("filename"), "mime_type": row.get("mime_type"),
            "attachment_type": row.get("attachment_type"),
            "extraction_status": row.get("extraction_status"),
            "checksum": row.get("checksum"),
            "extracted_text": (row.get("extracted_text") or "")[:3000],
        }
        for row in attachments
    ]
    # Addresses that cannot count as independent company confirmation: the
    # candidate's own mailbox, and the operator account that runs the tool.
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT email_address FROM candidate_mailboxes WHERE id=%s",
                    (finding.get("mailbox_id"),))
        row = cur.fetchone()
    finding["mailbox_email"] = row[0] if row else None
    finding["owned_addresses"] = [
        value for value in (
            finding["mailbox_email"],
            os.getenv("TELEAUTOMATION_OPERATOR_EMAIL"),
            "codetrust2025@gmail.com",
        ) if value
    ]
    body_all = " ".join(str(item.get("body") or "") for item in finding["thread"])
    finding["forwarded"] = bool(engine._FORWARD_MARKER.search(body_all)) or bool(
        engine._FORWARD_MARKER.search(str(finding.get("subject") or "")))
    return finding


# ── First-rollout eligibility ────────────────────────────────────────────────
#
# The audit does not review everything. It reviews the cases where a second
# opinion changes what an administrator would do: the ones the rules already
# doubt, the ones where the rules and the live pipeline disagree, and the ones
# whose consequences are largest.

ELIGIBILITY_SQL = """
    (
      f.outcome = 'MANUAL_REVIEW_REQUIRED'
      OR f.manual_review_required = true
      OR f.pipeline_agreement IN ('AUDIT_STRONGER','PIPELINE_STRONGER')
      OR f.outcome IN ('VERIFIED_OFFER_LETTER','OFFER_INDICATION')
      OR f.authenticity = 'SUSPICIOUS'
      OR c.status_mismatch = true
    )
"""


def eligible_findings(limit: int = 5) -> list[dict[str, Any]]:
    """Selection-audit findings worth a second opinion, highest value first.

    Deliberately excludes suppressed findings and anything already reviewed:
    this is a targeted first pass, not a backfill of every stored email.
    """
    from core import recruitment_mail_audit as audit_engine
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT f.id,f.canonical_candidate_id,f.subject,f.outcome,
                       f.authenticity,f.pipeline_agreement,f.manual_review_required,
                       c.candidate_name,c.status_mismatch
                FROM mail_outcome_audit_findings f
                LEFT JOIN mail_outcome_audit_candidates c
                  ON c.canonical_candidate_id = f.canonical_candidate_id
                WHERE COALESCE(f.suppressed,false) = false
                  AND f.outcome = ANY(%s)
                  AND {ELIGIBILITY_SQL}
                  AND NOT EXISTS (
                    SELECT 1 FROM mail_audit_ai_queue q WHERE q.finding_id = f.id)
                  AND NOT EXISTS (
                    SELECT 1 FROM mail_audit_ai_results r WHERE r.finding_id = f.id)
                ORDER BY
                  CASE f.outcome
                    WHEN 'VERIFIED_OFFER_LETTER' THEN 0
                    WHEN 'OFFER_INDICATION' THEN 1
                    WHEN 'MANUAL_REVIEW_REQUIRED' THEN 2
                    ELSE 3 END,
                  (f.authenticity = 'SUSPICIOUS') DESC,
                  f.received_at DESC NULLS LAST
                LIMIT %s""",
            (sorted(audit_engine.SELECTION_OUTCOMES), max(1, min(50, int(limit)))),
        )
        return _rows(cur)


def cached_result(key: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM mail_audit_ai_results WHERE cache_key=%s", (key,),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def _normalise(value: str) -> str:
    return " ".join(str(value or "").split()).lower()


# A Gmail message id is a hex string. "Ref:839093/1949677/ELTP 01-SEP-2021"
# is a payroll reference number the model lifted out of a PDF and presented as
# a message id; shape-checking rejects that class of fabrication outright.
_GMAIL_ID = re.compile(r"^[0-9a-f]{8,24}$")


def verify_review(finding: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Check the model's citations against the material it was actually given.

    A second opinion is only useful if it can be checked. Every claim must
    point at a real message, a real quotation inside that message, and a real
    attachment; anything else is recorded as a fabrication and the review is
    marked UNVERIFIED rather than silently believed.
    """
    problems: list[str] = []
    thread = finding.get("thread") or []
    attachments = finding.get("attachments") or []

    by_id = {str(item.get("message_id") or ""): item for item in thread}
    by_id.pop("", None)
    known_ids = set(by_id)
    if finding.get("provider_message_id"):
        known_ids.add(str(finding["provider_message_id"]))

    cited_id = str(payload.get("cited_message_id") or "").strip()
    if not cited_id:
        problems.append("No message id was cited.")
    elif not _GMAIL_ID.match(cited_id.lower()):
        problems.append(
            f"Cited message id '{cited_id[:60]}' is not a Gmail message id; it looks "
            "like a document reference lifted from the content."
        )
    elif cited_id not in known_ids:
        problems.append(f"Cited message id {cited_id} was not in the supplied timeline.")

    known_files = {_normalise(item.get("filename")): item for item in attachments}
    known_files.pop("", None)
    known_checksums = {str(item.get("checksum") or "") for item in attachments}
    known_checksums.discard("")

    cited_file = _normalise(payload.get("cited_attachment"))
    cited_attachment = None
    if cited_file:
        if not attachments:
            problems.append("An attachment was cited but the message has none.")
        elif cited_file not in known_files:
            problems.append(
                f"Cited attachment '{payload.get('cited_attachment')}' does not exist.")
        else:
            cited_attachment = known_files[cited_file]

    cited_hash = str(payload.get("cited_attachment_checksum") or "").strip()
    if cited_hash and cited_hash not in known_checksums:
        problems.append("Cited attachment checksum is not among the supplied evidence.")

    # The quote must appear in the material the model actually pointed at, not
    # merely somewhere in the bundle. Citing message A while quoting message B
    # is a citation that cannot be followed.
    quote = _normalise(payload.get("quoted_evidence"))
    if not quote:
        problems.append("No evidence was quoted.")
    else:
        # The union of what was explicitly cited: a model may reasonably cite
        # an attachment for context while quoting the covering email. What it
        # may not do is cite message A and quote message B, which is what the
        # narrow scope is here to catch.
        parts: list[str] = []
        labels: list[str] = []
        if cited_attachment is not None:
            parts.append(_normalise(cited_attachment.get("extracted_text")))
            labels.append("the cited attachment")
        if cited_id in by_id:
            source = by_id[cited_id]
            parts.append(_normalise(f"{source.get('subject') or ''} {source.get('body') or ''}"))
            labels.append("the cited message")
        if not parts:
            parts.append(_normalise(" ".join(
                [str(item.get("body") or "") for item in thread]
                + [str(item.get("extracted_text") or "") for item in attachments]
                + [str(finding.get("subject") or "")]
            )))
            labels.append("the supplied evidence")
        scope = " ".join(parts)
        scope_label = " or ".join(labels)
        head = quote[:60]
        if quote not in scope and (len(head) < 12 or head not in scope):
            problems.append(f"Quoted evidence does not appear in {scope_label}.")

    return {"trusted": not problems, "problems": problems,
            "checked_message_ids": sorted(known_ids),
            "checked_attachments": sorted(known_files)}


# ── Deterministic sender authenticity ────────────────────────────────────────

def sender_is_hiring_company(finding: dict[str, Any],
                             mailbox_email: str | None = None) -> dict[str, Any]:
    """Decide provenance from the headers, never from the model's assertion.

    The model claimed sender_is_hiring_company=true for a selection mail sent
    from the operator's own Gmail address. Whether a sender is the hiring
    company is a fact about domains, not a judgement.
    """
    sender = str(finding.get("sender_email") or "").strip().lower()
    root = engine.registrable_domain(engine.domain_of(sender))
    company_root = engine.registrable_domain(str(finding.get("company_domain") or ""))
    reasons: list[str] = []

    if not root:
        return {"is_company": False, "source": engine.SOURCE_UNKNOWN,
                "reasons": ["No sender domain."]}

    owned = {str(mailbox_email or "").strip().lower()}
    owned |= {str(value or "").strip().lower()
              for value in (finding.get("owned_addresses") or [])}
    owned.discard("")
    if sender in owned:
        reasons.append("Sent from the candidate's own or a TeleAutomation-owned mailbox.")
        return {"is_company": False, "source": engine.SOURCE_PERSONAL, "reasons": reasons}

    source = engine.classify_source(sender, finding.get("company_domain"))
    if source == engine.SOURCE_PORTAL:
        reasons.append(f"{root} is a job portal or applicant-tracking relay.")
        return {"is_company": False, "source": source, "reasons": reasons}
    if source == engine.SOURCE_PERSONAL:
        reasons.append(f"{root} is a free-mail domain and proves no company.")
        return {"is_company": False, "source": source, "reasons": reasons}

    forwarded = bool(finding.get("forwarded"))
    if forwarded:
        reasons.append("Forwarded mail: the original sender is not independently verified.")
        return {"is_company": False, "source": source, "reasons": reasons}

    if company_root and company_root == root:
        return {"is_company": True, "source": engine.SOURCE_COMPANY,
                "reasons": [f"Sender domain matches the stated company {company_root}."]}

    reasons.append(
        f"Sender {root} is a corporate domain but is not confirmed to be the hiring company."
        if not company_root else
        f"Sender {root} does not match the stated company {company_root}."
    )
    return {"is_company": False, "source": source, "reasons": reasons}


# ── Evidence-specific restrictions ───────────────────────────────────────────

_PAYSLIP_MARKERS = ("pay period", "payslip", "pay slip", "salary slip",
                    "employee code", "net pay", "provident fund", "earnings deductions")
_COMMUNITY_MARKERS = ("community", "forum", "newsletter", "unsubscribe",
                      "learning community", "certification", "training and certification",
                      "discussion", "subscribe")

# Outcomes that assert a company committed to hiring. These carry the most
# consequence and so face the most restrictions.
_HIGH_STAKES = frozenset({
    engine.FINAL_SELECTION, engine.VERIFIED_OFFER_LETTER, engine.JOINING_CONFIRMED,
})


def apply_evidence_restrictions(finding: dict[str, Any], payload: dict[str, Any],
                                *, provenance: dict[str, Any]) -> dict[str, Any]:
    """Downgrade a model conclusion the supplied evidence cannot support.

    Each rule below corresponds to a specific way the first batch went wrong.
    A restriction never raises an outcome; it only sends it to a human.
    """
    suggested = str(payload.get("suggested_outcome") or "").strip().upper()
    applied: list[str] = []
    if not suggested:
        return {"outcome": engine.MANUAL_REVIEW_REQUIRED, "restrictions": ["No outcome given."]}

    attachments = finding.get("attachments") or []
    corpus = _normalise(" ".join(
        [str(item.get("body") or "") for item in (finding.get("thread") or [])]
        + [str(item.get("extracted_text") or "") for item in attachments]
        + [str(finding.get("subject") or "")]
    ))
    attachment_text = _normalise(" ".join(
        str(item.get("extracted_text") or "") for item in attachments))

    payslip_only = (
        any(marker in attachment_text for marker in _PAYSLIP_MARKERS)
        and not any(marker in attachment_text
                    for marker in ("offer of employment", "appointment letter",
                                   "we are pleased to offer", "pleased to appoint"))
    )
    if suggested in _HIGH_STAKES and payslip_only:
        applied.append(
            "A payslip records existing employment and cannot prove selection, "
            "an offer or joining.")

    if suggested in _HIGH_STAKES and not provenance.get("is_company"):
        applied.append(
            "The sender is not confirmed to be the hiring company: "
            + " ".join(provenance.get("reasons") or []))

    if suggested in _HIGH_STAKES and any(
            marker in corpus for marker in _COMMUNITY_MARKERS):
        applied.append(
            "The message reads as a community, forum or newsletter mail rather "
            "than a hiring decision.")

    if payload.get("is_bulk_campaign") and suggested not in {
            engine.NOT_RELEVANT, engine.MANUAL_REVIEW_REQUIRED}:
        applied.append("A recruiter campaign cannot establish a round or an offer.")

    if suggested in _HIGH_STAKES and not str(finding.get("job_title") or "").strip():
        applied.append("No specific role is attached to this outcome.")
    if suggested in _HIGH_STAKES and not str(
            finding.get("company_name") or finding.get("company_domain") or "").strip():
        applied.append("No specific company is attached to this outcome.")

    if applied:
        return {"outcome": engine.MANUAL_REVIEW_REQUIRED, "restrictions": applied}
    return {"outcome": suggested, "restrictions": []}


# ── Approval presentation ────────────────────────────────────────────────────

AI_NOT_APPROVABLE = "AI suggestion — not eligible for approval."
NEEDS_MANUAL_REVIEW = "Needs manual review — deterministic evidence and the AI disagree."
SAFE_TO_REVIEW = "Safe to review for approval."


def approval_state(*, verified: bool, agreement: str, restrictions: list[str]) -> str:
    """What the UI may offer. Ollama alone never unlocks an approval."""
    if not verified:
        return AI_NOT_APPROVABLE
    if restrictions:
        return AI_NOT_APPROVABLE
    if agreement_requires_review(agreement):
        return NEEDS_MANUAL_REVIEW
    return SAFE_TO_REVIEW


def review_one(job: dict[str, Any]) -> dict[str, Any]:
    """Run one second opinion. Never raises into the caller's loop."""
    finding = _finding_for_job(str(job["finding_id"]))
    if not finding:
        _finish(str(job["id"]), QUEUE_DONE)
        return {"status": "SKIPPED", "reason": "finding no longer exists"}

    key = cache_key(finding)
    cached = cached_result(key)
    if cached:
        _finish(str(job["id"]), QUEUE_DONE)
        return {"status": "CACHED", "result": cached}

    from core.ai_gateway import chat_structured, AIGatewayError
    try:
        answer = chat_structured(
            messages=_prompt_payload(finding, finding.get("body_text") or ""),
            schema=AUDIT_SCHEMA,
            timeout=float(os.getenv("AI_MAIL_AUDIT_TIMEOUT_SECONDS", "90")),
            workload="mail_audit_review",
        )
        # chat_structured returns an AIResult whose .content is the validated
        # JSON *string*; the gateway has already checked it against the schema.
        raw = getattr(answer, "content", answer)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(payload, dict):
            raise AIGatewayError("Audit model returned a non-object response")
    except Exception as exc:
        # A failed or malformed audit review is contained here. It never
        # reaches the booking pipeline and never becomes an outcome.
        logger.warning("Audit second opinion failed finding_id=%s: %s",
                       job.get("finding_id"), exc)
        _finish(str(job["id"]), QUEUE_FAILED, error=str(exc))
        return {"status": "FAILED", "error": str(exc)[:400]}

    verification = verify_review(finding, payload)
    provenance = sender_is_hiring_company(finding, finding.get("mailbox_email"))
    restriction = apply_evidence_restrictions(finding, payload, provenance=provenance)
    agreement = derive_agreement(
        finding.get("outcome"), finding.get("pipeline_outcome"), restriction["outcome"],
    )
    # Confidence is only meaningful once the citations have been checked.
    confidence = normalize_confidence(payload.get("confidence")) if verification["trusted"] else None
    state = approval_state(verified=verification["trusted"], agreement=agreement,
                           restrictions=restriction["restrictions"])

    record = _store_result(
        key, finding, payload, model=str(getattr(answer, "model", "") or ""),
        verification=verification, provenance=provenance, restriction=restriction,
        agreement=agreement, confidence=confidence, approval=state,
    )
    _finish(str(job["id"]), QUEUE_DONE)
    return {"status": "COMPLETED", "result": record, "verification": verification,
            "agreement": agreement, "approval_state": state}


def _store_result(key: str, finding: dict[str, Any], payload: dict[str, Any],
                  *, model: str, verification: dict[str, Any] | None = None,
                  provenance: dict[str, Any] | None = None,
                  restriction: dict[str, Any] | None = None,
                  agreement: str = "", confidence: float | None = None,
                  approval: str = AI_NOT_APPROVABLE) -> dict[str, Any]:
    """Persist the second opinion. Advisory only: no finding is overwritten."""
    record_id = _id()
    verification = verification or {"trusted": False, "problems": ["not verified"]}
    provenance = provenance or {"is_company": False, "reasons": []}
    restriction = restriction or {"outcome": engine.MANUAL_REVIEW_REQUIRED, "restrictions": []}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_audit_ai_results
                 (id,cache_key,finding_id,prompt_name,model,agrees,suggested_outcome,
                  confidence,reasoning,is_bulk_campaign,sender_is_hiring_company,
                  quoted_evidence,cited_message_id,cited_attachment,cited_company,
                  verified,verification_problems,normalized_confidence,derived_agreement,
                  restricted_outcome,restrictions,sender_verified_company,approval_state,
                  raw_response)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT (cache_key) DO UPDATE SET
                 agrees=EXCLUDED.agrees,suggested_outcome=EXCLUDED.suggested_outcome,
                 confidence=EXCLUDED.confidence,reasoning=EXCLUDED.reasoning,
                 is_bulk_campaign=EXCLUDED.is_bulk_campaign,
                 sender_is_hiring_company=EXCLUDED.sender_is_hiring_company,
                 quoted_evidence=EXCLUDED.quoted_evidence,
                 cited_message_id=EXCLUDED.cited_message_id,
                 cited_attachment=EXCLUDED.cited_attachment,
                 cited_company=EXCLUDED.cited_company,
                 verified=EXCLUDED.verified,
                 verification_problems=EXCLUDED.verification_problems,
                 normalized_confidence=EXCLUDED.normalized_confidence,
                 derived_agreement=EXCLUDED.derived_agreement,
                 restricted_outcome=EXCLUDED.restricted_outcome,
                 restrictions=EXCLUDED.restrictions,
                 sender_verified_company=EXCLUDED.sender_verified_company,
                 approval_state=EXCLUDED.approval_state,
                 raw_response=EXCLUDED.raw_response,updated_at=now()
               RETURNING *""",
            (record_id, key, finding["id"], AUDIT_PROMPT_NAME, model,
             bool(payload.get("agrees")), str(payload.get("suggested_outcome") or ""),
             float(payload.get("confidence") or 0), str(payload.get("reasoning") or "")[:800],
             bool(payload.get("is_bulk_campaign")),
             bool(payload.get("sender_is_hiring_company")),
             str(payload.get("quoted_evidence") or "")[:400],
             str(payload.get("cited_message_id") or "")[:64],
             str(payload.get("cited_attachment") or "")[:200] or None,
             str(payload.get("company") or "")[:200] or None,
             bool(verification.get("trusted")),
             "; ".join(verification.get("problems") or [])[:600] or None,
             confidence,
             agreement or None,
             restriction.get("outcome"),
             "; ".join(restriction.get("restrictions") or [])[:900] or None,
             bool(provenance.get("is_company")),
             approval,
             json.dumps(payload, default=str)),
        )
        rows = _rows(cur)
    return rows[0] if rows else {}


def process_pending(limit: int | None = None, *, manual: bool = False) -> dict[str, Any]:
    """One audit pass. Yields to live processing and returns without work.

    Unattended passes additionally require AI_MAIL_AUDIT_AUTO_PROCESSING_ENABLED.
    A manual run from the API is unaffected by that flag, so an administrator
    can still audit on demand while automatic draining is paused.
    """
    if not manual and not auto_processing_enabled():
        return {"ran": 0, "deferred": True, "manual": False,
                "reason": f"{AUTO_PROCESSING_FLAG} is not enabled; "
                          "pending audit jobs are left untouched."}
    gate = may_run()
    if not gate["allowed"]:
        return {"ran": 0, "deferred": True, "reason": gate["reason"]}

    ensure_schema()
    budget = limit if limit is not None else max_concurrency()
    done = failed = 0
    for _ in range(max(1, budget)):
        # Re-check before every inference: live work that arrives mid-pass
        # takes the node back immediately.
        if not may_run()["allowed"]:
            return {"ran": done, "failed": failed, "deferred": True,
                    "reason": "Live processing resumed; audit yielded."}
        jobs = claim(limit=1)
        if not jobs:
            break
        outcome = review_one(jobs[0])
        if outcome["status"] == "FAILED":
            failed += 1
        else:
            done += 1
    return {"ran": done, "failed": failed, "deferred": False, "reason": ""}


def reset_for_rerun(finding_id: str, *, requested_by: str = "admin") -> None:
    """Queue one finding for another look. Audit tables only; nothing deleted."""
    ensure_schema()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_audit_ai_queue (id,finding_id,status,requested_by)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (finding_id) DO UPDATE SET
                 status='PENDING',retry_after=NULL,last_error=NULL,
                 requested_by=EXCLUDED.requested_by,updated_at=now()""",
            (_id(), finding_id, QUEUE_PENDING, requested_by),
        )


def queue_status() -> dict[str, Any]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status,count(*) AS total FROM mail_audit_ai_queue GROUP BY 1",
        )
        counts = {row["status"]: int(row["total"]) for row in _rows(cur)}
        cur.execute("SELECT count(*) FROM mail_audit_ai_results")
        results = int(cur.fetchone()[0] or 0)
    return {"enabled": enabled(), "auto_processing": auto_processing_enabled(),
            "queue": counts, "results": results,
            "concurrency": max_concurrency(), "gate": may_run()}


def results_for_findings(finding_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not finding_ids:
        return {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM mail_audit_ai_results WHERE finding_id = ANY(%s)",
            (finding_ids,),
        )
        return {str(row["finding_id"]): row for row in _rows(cur)}


def disagreements(limit: int = 100) -> list[dict[str, Any]]:
    """Findings where the model and the rule engine differ — the useful output."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT r.*,f.subject,f.sender_email,f.outcome AS rule_outcome,
                      f.canonical_candidate_id,c.candidate_name
               FROM mail_audit_ai_results r
               JOIN mail_outcome_audit_findings f ON f.id=r.finding_id
               LEFT JOIN mail_outcome_audit_candidates c
                 ON c.canonical_candidate_id=f.canonical_candidate_id
               WHERE r.agrees=false
               ORDER BY r.confidence DESC LIMIT %s""",
            (max(1, min(500, int(limit))),),
        )
        return _rows(cur)
