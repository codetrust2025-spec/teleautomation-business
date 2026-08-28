"""Precision-first selection and offer email detection."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any

from core import recruitment_mail_store as store
from core.ai_gateway import AIGatewayError, chat_structured, configured_models
from services.recruitment_semantics import (
    DOCUMENT_TYPES,
    EMAIL_INTENTS,
    classify_context,
    extract_interview_schedule,
    redact_sensitive_text,
    validate_interview_event,
    validate_lifecycle_event,
)

logger = logging.getLogger(__name__)


def _publish(event_type: str, **payload: Any) -> None:
    try:
        from core.recruitment_realtime import publish
        publish(event_type, **payload)
    except Exception:
        # Persistence remains the source of truth; real-time delivery is best
        # effort and API recovery will return missed notifications.
        logger.debug("Mail real-time event unavailable type=%s", event_type, exc_info=True)


def _publish_ignored_interview(
    mailbox: dict[str, Any],
    decoded: dict[str, Any],
    attachments: list[dict[str, Any]] | None,
    status: str,
    reason: str,
) -> dict[str, Any] | None:
    """Surface a mail that was dropped while carrying an interview.

    Both the duplicate check and the routing filter drop a message by returning
    early, which wrote a processing_status on the row and nothing else — no
    notification, nothing on any screen. An invite could therefore disappear
    with no trace an operator would ever see, which is exactly how a Sourcebae
    invite for a 4:15pm interview went missing.

    Only messages that still parse as an interview are surfaced, so ordinary
    marketing noise being filtered does not become operator work. Returns the
    interview signal it found, for callers that want to log it.
    """
    try:
        from services.calendar_invite_parser import trusted_interview_result

        signal = trusted_interview_result(decoded, list(attachments or []))
    except Exception:  # a broken parser must not break ingestion
        logger.debug("Interview signal check failed", exc_info=True)
        return None
    if not signal:
        return None

    logger.warning(
        "Interview-bearing mail dropped candidate=%s status=%s reason=%s subject=%r date=%s time=%s",
        mailbox.get("candidate_id"), status, reason,
        str(decoded.get("subject") or "")[:120],
        signal.get("interview_date"), signal.get("interview_time"),
    )
    _publish(
        "interview_mail_ignored",
        candidate_id=mailbox.get("candidate_id"),
        gmail_message_id=decoded.get("provider_message_id"),
        subject=decoded.get("subject"),
        processing_status=status,
        reason=reason,
        interview_date=signal.get("interview_date"),
        interview_time=signal.get("interview_time"),
    )
    return signal


def _failure_review_result(message: dict[str, Any], exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", None) or type(exc).__name__
    context = classify_context(
        str(message.get("subject") or ""), str(message.get("body") or ""),
        sender_email=str(message.get("sender_email") or ""),
        sent_at=message.get("sent_at"), attachments=message.get("attachments") or [],
    )
    is_deterministic_noise = bool(
        context["is_questionnaire"] or context["is_promotional_or_job_ad"]
        or context["is_historical_information"] or context.get("is_question")
    )
    if is_deterministic_noise:
        # The deterministic filter already conclusively classified this
        # message as non-actionable noise (ad/questionnaire/historical
        # document/question). Ollama is not required to reject noise, so an
        # AI outage must never force this into Needs Review or retry-pending
        # — it goes straight to the same audit-only path a healthy AI run
        # would have produced.
        return {
            "schema_version": "selection_offer_event_v1",
            "is_recruitment_related": True,
            "is_selection_or_offer_related": False,
            "should_create_review_record": False,
            "status": "IGNORED_NOT_OFFER_RELATED",
            "primary_status": "IGNORED_NOT_OFFER_RELATED",
            "classification": "not_relevant",
            "candidate_status": "Profile Active",
            "confidence": 0.0,
            "ignore_reason": context["email_intent"],
            "reason": f"Deterministic noise filter classified this email as {context['email_intent']}; AI analysis was not required.",
            "candidate": {"name": None, "email": message.get("recipient_email")},
            "company": {"name": None, "domain": None},
            "job": {"title": None, "employment_type": None, "location": None},
            "recruiter": {"name": message.get("sender_name"), "email": message.get("sender_email")},
            "interview": {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")},
            "offer": {"offer_detected": False, "offer_letter_detected": False,
                      "appointment_letter_detected": False, "offer_date": None,
                      "offered_ctc": None, "currency": None, "joining_date": None,
                      "offer_expiry_date": None},
            "attachments": [], "evidence": [], "risk_flags": [],
            "requires_manual_review": False,
            "summary": context["evidence_summary"],
            "recommended_action": "No action required; this message was classified as non-actionable noise.",
            "classification_source": "DETERMINISTIC_NOISE_FILTER",
            "ai_validation_status": "NOT_REQUIRED",
            "ai_status": "NOT_REQUIRED",
            "validation_status": "NOT_REQUIRED",
            "email_intent": context["email_intent"],
            "document_type": context["document_type"],
            "is_candidate_specific": context["is_candidate_specific"],
            "is_job_outcome": False,
            "is_current_event": False,
            "is_questionnaire": context["is_questionnaire"],
            "is_promotional_or_job_ad": context["is_promotional_or_job_ad"],
            "is_historical_information": context["is_historical_information"],
            "historical_employment_evidence": context["historical_employment_evidence"],
            "lifecycle_event": "NONE",
            "interview_event": "NONE",
            "business_domain": "NONE",
            "evidence_summary": context["evidence_summary"],
        }
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": "MANUAL_REVIEW_REQUIRED",
        "primary_status": "MANUAL_REVIEW_REQUIRED",
        "classification": "needs_review",
        "candidate_status": "Needs Review",
        "confidence": 0.0,
        "ignore_reason": None,
        "reason": f"AI validation unavailable ({code})",
        "candidate": {"name": None, "email": message.get("recipient_email")},
        "company": {"name": None, "domain": None},
        "job": {"title": None, "employment_type": None, "location": None},
        "recruiter": {"name": message.get("sender_name"), "email": message.get("sender_email")},
        "interview": {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")},
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [], "evidence": [], "risk_flags": ["AI_VALIDATION_UNAVAILABLE"],
        "requires_manual_review": True,
        "summary": "AI validation is unavailable; this recruitment email requires administrator review.",
        "recommended_action": "Review the email metadata and evidence, then retry AI analysis or correct the result manually.",
        "classification_source": "FAILURE_REVIEW",
        "ai_validation_status": "RETRY_PENDING",
        "ai_status": "RETRY_PENDING",
        "validation_status": "RETRY_PENDING",
        "email_intent": context["email_intent"],
        "document_type": context["document_type"],
        "is_candidate_specific": context["is_candidate_specific"],
        "is_job_outcome": False,
        "is_current_event": False,
        "is_questionnaire": context["is_questionnaire"],
        "is_promotional_or_job_ad": context["is_promotional_or_job_ad"],
        "is_historical_information": context["is_historical_information"],
        "historical_employment_evidence": context["historical_employment_evidence"],
        "lifecycle_event": "NONE",
        "evidence_summary": "AI analysis could not complete. No candidate lifecycle event was created; retry is pending.",
    }

VISIBLE_STATUSES = [
    "SELECTED", "FINAL_SELECTION_CONFIRMED", "FINAL_ROUND_CLEARED", "OFFER_INDICATION",
    "OFFER_IN_PROGRESS", "OFFER_APPROVED", "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED",
    "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED", "JOINING_CONFIRMED",
    "JOINED", "POST_SELECTION_ONBOARDING", "OFFER_DECLINED", "OFFER_REVOKED",
    "JOINING_DATE_UPDATED", "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION",
    "HR_CONFIRMATION", "COMPENSATION_CONFIRMATION", "INTERVIEW_UPDATE", "INTERVIEW_SHORTLISTED",
    "INTERVIEW_PROPOSED", "OFFER_NEEDS_REVIEW", "JOINING_NEEDS_REVIEW", "SELECTION_NEEDS_REVIEW",
    "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
    "CANDIDATE_REJECTED", "MANUAL_REVIEW_REQUIRED",
]
INTERNAL_STATUSES = ["IGNORED_NOT_OFFER_RELATED", "IGNORED_LOW_CONFIDENCE"]
STATUSES = VISIBLE_STATUSES + INTERNAL_STATUSES
TRACKED_STATUSES = set(VISIBLE_STATUSES)
OFFER_CASE_STATUSES = {
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED",
    "JOINING_CONFIRMED", "JOINED", "POST_SELECTION_ONBOARDING",
}

STATUS_PRIORITY = [
    "JOINED", "JOINING_CONFIRMED", "POST_SELECTION_ONBOARDING",
    "OFFER_ACCEPTED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED",
    "OFFER_APPROVED", "OFFER_IN_PROGRESS", "FINAL_SELECTION_CONFIRMED",
    "OFFER_REVOKED", "OFFER_DECLINED", "JOINING_DATE_UPDATED",
    "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION", "HR_CONFIRMATION",
    "COMPENSATION_CONFIRMATION", "CANDIDATE_REJECTED",
    "INTERVIEW_CANCELLED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CONFIRMED",
    "FINAL_ROUND_CLEARED", "SELECTED", "OFFER_INDICATION", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE", "SHORTLISTED",
]

STATUS_SIGNALS = [
    ("JOINED", ("welcome aboard", "welcome to the organization", "reported for joining", "joined the company", "employment commenced")),
    ("JOINING_CONFIRMED", ("your date of joining will be", "your joining date is", "date of joining", "expected joining date", "please join on", "report for joining on", "reporting date", "joining is confirmed", "joining confirmed", "welcome to the team")),
    ("POST_SELECTION_ONBOARDING", ("employee onboarding", "post-selection onboarding", "complete onboarding formalities", "complete pre-joining formalities", "pre-joining formalities", "onboarding has started", "complete onboarding before joining")),
    ("OFFER_ACCEPTED", ("offer acceptance", "accepted your offer", "accept the offer", "offer has been accepted")),
    ("OFFER_DECLINED", ("declined the offer", "offer has been declined", "will not accept the offer")),
    ("OFFER_REVOKED", ("offer has been revoked", "withdrawn the offer", "offer stands withdrawn", "offer is rescinded")),
    ("JOINING_DATE_UPDATED", ("revised joining date", "joining date has changed", "joining date has moved", "updated date of joining", "new joining date")),
    ("BACKGROUND_VERIFICATION", ("background verification", "pre-employment verification", "background check", "digital employment", "digiverifier", "bgv_", "loa accepetence", "loa acceptance")),
    ("HR_CONFIRMATION", ("minimal documents", "capgemini documenation", "capgemini documentation", "documents required for offer", "documents required - ey", "documents required for onboarding", "pre-offer documents", "pre-offer document", "uan number and updated cv", "post selection document", "ltimindtree selection process - pre-offer")),
    ("COMPENSATION_CONFIRMATION", ("compensation confirmation", "confirmed compensation", "annual ctc is", "salary package is")),
    ("FINAL_ROUND_CLEARED", ("cleared the final round", "cleared all rounds", "cleared the technical round", "successfully cleared the l1", "successfully cleared the l2", "cleared the l1 round", "cleared the l2 round", "cleared the l1", "cleared the l2", "cleared l1", "cleared l2", "final round cleared")),
    # Shortlisting is an outcome in its own right, not only a preamble to an
    # interview. These phrases used to require the word "interview" right after
    # "shortlisted for", so a plain selection mail — "your profile is
    # provisionally shortlisted for Python Django with <company>" — matched
    # nothing here, fell through to the model, and was ignored for carrying no
    # interview date or time. Every phrase below states the selection outcome
    # explicitly, so a bare document request with no such wording still cannot
    # reach this status.
    ("INTERVIEW_SHORTLISTED", (
        "shortlisted for the next interview", "shortlisted for the technical interview",
        "shortlisted for hr interview",
        "provisionally shortlisted", "profile is shortlisted",
        "profile has been shortlisted", "profile is provisionally shortlisted",
        "you have been shortlisted", "you are shortlisted",
        "we have shortlisted your", "shortlisted for the role",
        "shortlisted for the position", "candidature has been shortlisted",
        "candidature has been provisionally shortlisted",
        "shortlisted for further discussion", "shortlisted for hr discussion",
        "moved forward to the next stage", "moving forward to the hr round",
    )),
    # These phrases route interview mail to Ollama but never determine the
    # actionable outcome. Only the validated contextual model may upgrade an
    # update to confirmed/rescheduled/cancelled.
    ("INTERVIEW_UPDATE", ("interview invitation", "interview scheduled", "interview has been scheduled", "interview confirmed", "interview rescheduled", "interview cancelled", "technical interview", "technical round", "managerial interview", "hr interview", "hr round")),
    ("CANDIDATE_REJECTED", ("regret to inform", "not moving forward", "not selected for the role", "application was unsuccessful")),
    ("APPOINTMENT_LETTER_RECEIVED", ("appointment letter attached", "letter of appointment", "appointment letter")),
    ("OFFER_LETTER_RECEIVED", (
        "offer letter attached", "find your offer letter",
        "offer letter has been released", "offer has been released",
        "offer has been successfully released", "employment offer attached",
        "offer of employment", "offer letter inside",
        "congratulations, you're in! offer letter", "deployment with", "fulltime with",
    )),
    ("OFFER_APPROVED", ("offer has been approved", "offer is approved", "offer approved")),
    ("OFFER_IN_PROGRESS", ("offer is currently being processed", "processing your offer", "offer is being prepared", "offer under preparation")),
    ("FINAL_SELECTION_CONFIRMED", ("final selection confirmed", "selection has been confirmed", "finally selected")),
    ("SELECTED", ("you have been selected", "you are selected", "selected for the position", "selected for the role", "congratulations on your selection", "selected for the post", "selection confirmation", "shortlisted for offer", "shortlisted for the offer")),
    ("OFFER_INDICATION", ("we are pleased to offer you", "we are delighted to offer you", "we would like to offer you", "planning to release your offer", "intent to offer", "employment offer", "compensation offered", "annual ctc offered")),
    ("SHORTLISTED", ("you have been shortlisted", "being shortlisted", "shortlisted for the role", "shortlisted for the position")),
]

NOISE_RULES = [
    ("JOB_RECOMMENDATION", ("job recommendation", "recommended jobs", "jobs matching your profile", "new jobs for you", "jobs for you", "similar jobs", "suggested opportunities")),
    ("JOB_ALERT", ("job alert", "hiring alert", "featured jobs", "new openings", "daily job", "weekly job")),
    ("JOB_PORTAL_MARKETING", ("apply now", "increase profile visibility", "upgrade account", "premium subscription", "career newsletter", "unsubscribe")),
    ("PROFILE_NOTIFICATION", ("resume viewed", "profile viewed", "searched your profile")),
    ("APPLICATION_UPDATE", ("application received", "thank you for applying", "application submitted", "application under review")),
    ("ASSESSMENT", ("assessment invitation", "coding test invitation", "complete the assessment")),
    ("INTERVIEW", ("interview invitation", "interview scheduled", "interview has been scheduled", "interview rescheduled", "interview reminder", "interview cancelled", "technical round", "hr round")),
    ("REJECTION", ("not selected", "regret to inform", "not moving forward", "rejection")),
]

# A job board links every advert it lists. Two or more distinct postings in one
# mail is a catalogue, whoever sent it and however it is worded.
_JOB_POSTING_LINK = re.compile(
    r"(?:linkedin\.com/comm)?/jobs/view/(\d+)"          # LinkedIn
    r"|naukri\.com/job-listings-[\w-]*?(\d{6,})"        # Naukri
    r"|/job(?:s)?/(\d{6,})/(?:apply|view)",             # Indeed / Monster shapes
    re.I,
)

# Wording a catalogue uses about itself. Kept as a secondary signal only: the
# exact phrasing changes without notice — LinkedIn writes "Jobs that match your
# profile" while NOISE_RULES only knew "jobs matching your profile", and that
# one word is why a six-advert digest was sent to the model as though it might
# be news about this candidate.
_JOB_DIGEST_MARKERS = (
    "jobs that match your profile", "jobs matching your profile",
    "jobs picked for you", "jobs for you", "recommended jobs",
    "based on your title and location", "view job:", "see all jobs",
    "similar jobs", "job alert", "new jobs posted",
)

_JOB_BOARD_SENDERS = (
    "jobs-noreply@linkedin.com", "jobalerts-noreply@linkedin.com",
    "jobs-listings@linkedin.com", "info@naukri.com", "alerts@naukri.com",
    "jobalerts@naukri.com", "noreply@indeed.com", "alert@indeed.com",
    "no-reply@monsterindia.com", "jobs@shine.com",
)

# Aggregators and career-marketing platforms. No employer lifecycle mail
# originates from these domains: they send "your profile was shortlisted",
# "jobs found for you" and profile-completion nags, phrased exactly like a real
# status update because that is what makes them worth opening.
#
# Matched by domain, not address. The list above is by address, which is why it
# missed every one of these in production: it names `jobs@shine.com` while the
# mail actually arrives from `alerts@jobs.shine.com`, and talent500 alone sent
# 29 of them from `aditi@talent500.co`. Any new mailbox at the same company
# would have needed its own entry.
#
# Deliberately excluded, because these are real senders whose mail must keep
# flowing: employer domains, and applicant-tracking systems such as ripplehire,
# curatal, myworkday, ambitionhire and wecreateproblems. An ATS relays genuine
# employer decisions and is not an aggregator.
_JOB_BOARD_DOMAINS = (
    "talent500.co",
    "timesjobs.com",
    "shine.com",
    "indeed.com",
    "naukri.com",
    "monsterindia.com",
    "abekus.co",
    "yocket.in",
)

# LinkedIn is kept to specific mailboxes rather than the whole domain: a
# recruiter's InMail can carry a real conversation, so blocking linkedin.com
# outright would lose genuine mail to stop notification digests.
_JOB_BOARD_ADDRESSES = _JOB_BOARD_SENDERS + (
    "notifications-noreply@linkedin.com",
    "messages-noreply@linkedin.com",
)


def job_board_notification(sender_email: str) -> bool:
    """True when the sender is an aggregator, not an employer or its ATS.

    These mails are the single largest source of false lifecycle statuses. A
    talent500 "Shortlisted but your profile is incomplete" and a timesjobs
    "Your profile has been Shortlisted for EMBA" both read to a model as a
    genuine shortlisting, and were classified as one; the routing gate then
    refused them for lack of corroborating evidence, which is why they never
    reached an operator. Stopping them here means the gate no longer has to be
    the thing standing between marketing and the alert queue.
    """
    address = str(sender_email or "").strip().casefold()
    if not address:
        return False
    if address in _JOB_BOARD_ADDRESSES:
        return True
    domain = address.rpartition("@")[2]
    if not domain:
        return False
    return any(
        domain == known or domain.endswith("." + known)
        for known in _JOB_BOARD_DOMAINS
    )


def job_advertisement_digest(
    subject: str, body: str, sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> bool:
    """True when the mail is a list of vacancies, not news about this candidate.

    A catalogue of adverts contains company names, role titles and locations, so
    a model asked "what happened to this candidate?" can assemble a convincing
    answer out of two unrelated listings. That is exactly what happened: a
    LinkedIn digest of six vacancies produced INTERVIEW_SHORTLISTED at 95%
    confidence, "Birlasoft and FactSet", and a summary saying the candidate had
    been shortlisted and should prepare for an interview. Every quoted evidence
    string was verbatim — they were the advert lines themselves — so the
    verbatim check could not catch it. The claim was about *meaning*, and the
    meaning was never in the mail.

    The test is structural rather than phrase-based, because the phrasing is the
    part that changes. Nothing here reads the model's answer.
    """
    text = " ".join(
        [str(subject or ""), str(body or "")]
        + [str(item.get("text") or "") for item in (attachments or [])]
    )
    postings = {
        next(group for group in match.groups() if group)
        for match in _JOB_POSTING_LINK.finditer(text)
    }
    if len(postings) >= 2:
        return True
    sender = str(sender_email or "").strip().casefold()
    if sender in _JOB_BOARD_SENDERS:
        haystack = text.casefold()
        return any(marker in haystack for marker in _JOB_DIGEST_MARKERS)
    return False


SPECIAL_CONTEXT = {
    "BACKGROUND_VERIFICATION": ("background verification", "pre-employment verification", "document verification"),
    "SALARY": ("salary discussion", "compensation discussion", "ctc discussion"),
    "JOINING_REQUEST": ("confirm your date of joining", "please confirm your joining date"),
}

SCHEMA = {
    "type": "object",
    "required": [
        "schema_version", "is_recruitment_related", "is_selection_or_offer_related",
        "should_create_review_record", "status", "confidence", "ignore_reason",
        "candidate", "company", "job", "recruiter", "interview", "offer",
        "attachments", "evidence", "risk_flags", "requires_manual_review",
        "summary", "reason", "recommended_action", "classification", "candidate_status",
        "email_intent", "document_type", "is_candidate_specific", "is_job_outcome",
        "is_current_event", "is_questionnaire", "is_promotional_or_job_ad",
        "is_historical_information", "lifecycle_event", "evidence_summary",
        "business_domain", "interview_event",
    ],
    "properties": {
        "schema_version": {"const": "selection_offer_event_v1"},
        "is_recruitment_related": {"type": "boolean"},
        "is_selection_or_offer_related": {"type": "boolean"},
        "should_create_review_record": {"type": "boolean"},
        "status": {"type": "string", "enum": STATUSES},
        "classification": {"type": "string", "enum": sorted(store.CANONICAL_CLASSIFICATIONS)},
        "candidate_status": {"type": "string", "enum": sorted(set(store._CLASSIFICATION_STATUS.values()))},
        "email_intent": {"type": "string", "enum": sorted(EMAIL_INTENTS)},
        "document_type": {"type": "string", "enum": sorted(DOCUMENT_TYPES)},
        "is_candidate_specific": {"type": "boolean"},
        "is_job_outcome": {"type": "boolean"},
        "is_current_event": {"type": "boolean"},
        "is_questionnaire": {"type": "boolean"},
        "is_promotional_or_job_ad": {"type": "boolean"},
        "is_historical_information": {"type": "boolean"},
        "historical_employment_evidence": {"type": "boolean"},
        "lifecycle_event": {"type": "string"},
        "business_domain": {"type": "string", "enum": ["SELECTION_TRACKING", "INTERVIEW_TRACKING", "NONE"]},
        "interview_event": {"type": "string", "enum": ["NONE", "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"]},
        "evidence_summary": {"type": "string", "maxLength": 1000},
        "validation_status": {"type": "string"},
        "ai_status": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "ignore_reason": {"type": ["string", "null"]},
        "candidate": {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}}, "required": ["name", "email"]},
        "company": {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "domain": {"type": ["string", "null"]}}, "required": ["name", "domain"]},
        "job": {"type": "object", "properties": {"title": {"type": ["string", "null"]}, "employment_type": {"type": ["string", "null"]}, "location": {"type": ["string", "null"]}}, "required": ["title", "employment_type", "location"]},
        "recruiter": {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}}, "required": ["name", "email"]},
        "interview": {"type": "object", "properties": {
            **{key: {"type": ["string", "null"]} for key in [
                "date", "time", "end_time", "timezone", "mode", "round", "location", "meeting_link",
                "original_date", "original_time", "original_timezone",
            ]},
            "duration_minutes": {"type": ["integer", "null"], "minimum": 5, "maximum": 720},
        }, "required": ["date", "time", "timezone", "mode", "round", "location", "meeting_link"]},
        "offer": {"type": "object", "properties": {
            "offer_detected": {"type": "boolean"}, "offer_letter_detected": {"type": "boolean"},
            "appointment_letter_detected": {"type": "boolean"}, "offer_date": {"type": ["string", "null"]},
            "offered_ctc": {"type": ["number", "null"]}, "currency": {"type": ["string", "null"]},
            "joining_date": {"type": ["string", "null"]}, "offer_expiry_date": {"type": ["string", "null"]},
        }, "required": ["offer_detected", "offer_letter_detected", "appointment_letter_detected", "offer_date", "offered_ctc", "currency", "joining_date", "offer_expiry_date"]},
        "attachments": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}, "filename": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["type", "filename", "confidence"]}},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string", "enum": ["EMAIL_SUBJECT", "EMAIL_BODY", "ATTACHMENT", "THREAD_CONTEXT"]},
            "meaning": {"type": "string"}, "text": {"type": "string", "minLength": 3, "maxLength": 500},
        }, "required": ["source", "meaning", "text"]}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "requires_manual_review": {"type": "boolean"}, "summary": {"type": "string", "maxLength": 1000},
        "reason": {"type": "string", "maxLength": 1000},
        "recommended_action": {"type": "string", "maxLength": 1000},
        "model_validation": {"type": "object"},
    },
    "additionalProperties": False,
}


def clean_email(text: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    value = re.split(r"(?im)^\s*(?:on .+ wrote:|from:|unsubscribe|confidentiality notice)", value, maxsplit=1)[0]
    return re.sub(r"\s+", " ", value).strip()[:30000]


def _source_texts(subject: str, body: str, attachments: list[dict[str, Any]] | None, thread_context: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    return {
        "EMAIL_SUBJECT": [clean_email(subject)],
        "EMAIL_BODY": [clean_email(body)],
        "ATTACHMENT": [clean_email(str(item.get("text") or "")) for item in (attachments or [])],
        "THREAD_CONTEXT": [clean_email(" ".join(str(item.get(key) or "") for key in ("subject", "body"))) for item in (thread_context or [])[-5:]],
    }


def _matching_statuses(text: str) -> list[tuple[str, str]]:
    lowered = text.lower()
    matches = []
    for status, phrases in STATUS_SIGNALS:
        for phrase in phrases:
            if phrase in lowered:
                # Large employers commonly append a disclaimer saying that an
                # interview message must *not* be treated as an offer of
                # employment.  The phrase alone is therefore not positive
                # offer evidence when it occurs inside that negated clause.
                if status in {"OFFER_LETTER_RECEIVED", "OFFER_INDICATION"} and phrase in {
                    "employment offer", "offer of employment",
                }:
                    start = max(0, lowered.find(phrase) - 220)
                    end = min(len(lowered), lowered.find(phrase) + len(phrase) + 220)
                    context = lowered[start:end]
                    if (
                        "unless there is a formal offer" in context
                        and any(token in context for token in (
                            "shall not be assumed", "not be assumed",
                            "not be treated", "no guarantee of employment",
                        ))
                    ):
                        continue
                if status in {"APPOINTMENT_LETTER_RECEIVED", "OFFER_LETTER_RECEIVED"}:
                    position = lowered.find(phrase)
                    context = lowered[max(0, position - 240):position + len(phrase) + 240]
                    requested_history = any(token in context for token in (
                        "previous companies", "previous company", "mandatory checklist",
                        "documents are required", "please upload", "please submit", "required documents",
                    ))
                    actual_outcome = any(token in context for token in (
                        "attached", "we are pleased to appoint", "your appointment letter",
                        "we are pleased to offer", "offer letter has been released",
                    ))
                    if requested_history and not actual_outcome:
                        continue
                matches.append((status, phrase))
                break
    return matches


def _evidence_excerpt(text: str, phrase: str) -> str:
    clean = clean_email(text)
    start = clean.casefold().find(phrase.casefold())
    if start < 0:
        return phrase
    left = max(clean.rfind(".", 0, start), clean.rfind("!", 0, start), clean.rfind("?", 0, start)) + 1
    endings = [pos for mark in ".!?" if (pos := clean.find(mark, start)) >= 0]
    right = min(endings) + 1 if endings else min(len(clean), start + 240)
    return clean[left:right].strip()[:500]


def _extract_joining_date(text: str) -> str | None:
    month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    patterns = [rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month})\s*,?\s*(\d{{4}})\b", rf"\b({month})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})\b"]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        parts = match.groups()
        candidate = " ".join(parts if index == 0 else (parts[1], parts[0], parts[2]))
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def _extract_context(subject: str, body: str, sender_email: str) -> tuple[str | None, str | None, str | None]:
    job = None
    for pattern in (r"\brole of\s+([A-Za-z][A-Za-z0-9 /&+.#-]{1,80}?)(?=[.,;\n]|\byour date\b)", r"[-–—]\s*([A-Za-z][A-Za-z0-9 /&+.#-]{1,80}?)\s+Role\b"):
        match = re.search(pattern, subject + "\n" + body, re.I)
        if match:
            job = match.group(1).strip(" -–—")
            break
    company = None
    company_match = re.search(r"\b([A-Z][A-Z0-9 &.,'-]{2,100}?(?:PVT\.?\s*LTD\.?|PRIVATE LIMITED|SERVICES INDIA PVT\.?\s*LTD\.?|LIMITED))\b", body)
    if company_match:
        company = re.sub(r"\s+", " ", company_match.group(1)).strip()
    domain = sender_email.rsplit("@", 1)[-1].lower() if "@" in sender_email else None
    return company, job, domain


def prefilter_decision(subject: str, body: str, sender_name: str = "", sender_email: str = "", attachments: list[dict[str, Any]] | None = None, thread_context: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sources = _source_texts(subject, body, attachments, thread_context)
    semantic = classify_context(
        subject, body, sender_email=sender_email, attachments=attachments,
    )
    if (
        semantic["is_questionnaire"]
        or semantic["is_promotional_or_job_ad"]
        or semantic["is_historical_information"]
        or semantic.get("is_question")
    ):
        haystack = " ".join([subject, sender_name, sender_email, body]).lower()
        for reason, phrases in NOISE_RULES:
            if reason in {"JOB_RECOMMENDATION","JOB_ALERT","JOB_PORTAL_MARKETING","PROFILE_NOTIFICATION","APPLICATION_UPDATE","ASSESSMENT"} and any(phrase in haystack for phrase in phrases):
                return {"qualified": False, "score": 0.0, "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [], "ignore_reason": reason}
        return {
            "qualified": False, "score": 0.0,
            "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [],
            "ignore_reason": semantic["email_intent"], "semantic_context": semantic,
        }
    evidence = []
    detected = []
    for source, values in sources.items():
        for value in values:
            for status, phrase in _matching_statuses(value):
                detected.append(status)
                evidence.append({"source": source, "meaning": status, "text": _evidence_excerpt(value, phrase)})
    for attachment in attachments or []:
        filename = str(attachment.get("filename") or "").lower()
        attachment_text = clean_email(str(attachment.get("text") or ""))
        lowered_text = attachment_text.lower()
        if "appointment" in filename and any(token in lowered_text for token in ("employment", "appointed", "appointment")):
            detected.append("APPOINTMENT_LETTER_RECEIVED")
            evidence.append({"source": "ATTACHMENT", "meaning": "APPOINTMENT_LETTER_RECEIVED", "text": next(token for token in ("employment", "appointed", "appointment") if token in lowered_text)})
        elif "offer" in filename and any(token in lowered_text for token in ("employment offer", "offered employment", "offer of employment")):
            detected.append("OFFER_LETTER_RECEIVED")
            evidence.append({"source": "ATTACHMENT", "meaning": "OFFER_LETTER_RECEIVED", "text": next(token for token in ("employment offer", "offered employment", "offer of employment") if token in lowered_text)})
    direct_text = " ".join(sources["EMAIL_SUBJECT"] + sources["EMAIL_BODY"])
    if "JOINING_CONFIRMED" in detected and not _extract_joining_date(direct_text) and "please confirm your date of joining" in direct_text.lower():
        detected=[value for value in detected if value!="JOINING_CONFIRMED"]
        evidence=[item for item in evidence if item.get("meaning")!="JOINING_CONFIRMED"]
    combined_context = " ".join(sources["EMAIL_BODY"] + sources["ATTACHMENT"] + sources["THREAD_CONTEXT"]).lower()
    has_confirmed_context = any(token in combined_context for token in ("selected", "selection confirmed", "offer letter", "employment offer", "offer approved", "onboarding"))
    for source, values in sources.items():
        for value in values:
            lowered = value.lower()
            if has_confirmed_context and any(phrase in lowered for phrase in SPECIAL_CONTEXT["BACKGROUND_VERIFICATION"]):
                detected.append("POST_SELECTION_ONBOARDING")
                phrase = next(p for p in SPECIAL_CONTEXT["BACKGROUND_VERIFICATION"] if p in lowered)
                evidence.append({"source": source, "meaning": "POST_SELECTION_ONBOARDING", "text": phrase})
            if has_confirmed_context and any(phrase in lowered for phrase in SPECIAL_CONTEXT["SALARY"]):
                detected.append("OFFER_INDICATION")
                phrase = next(p for p in SPECIAL_CONTEXT["SALARY"] if p in lowered)
                evidence.append({"source": source, "meaning": "OFFER_INDICATION", "text": phrase})
            if has_confirmed_context and any(phrase in lowered for phrase in SPECIAL_CONTEXT["JOINING_REQUEST"]):
                detected.append("JOINING_CONFIRMED")
                phrase = next(p for p in SPECIAL_CONTEXT["JOINING_REQUEST"] if p in lowered)
                evidence.append({"source": source, "meaning": "JOINING_CONFIRMED", "text": phrase})
    if detected:
        status = next((candidate for candidate in STATUS_PRIORITY if candidate in detected), detected[0])
        # A shortlist by itself remains ordinary recruitment noise. Stronger
        # evidence later in the complete message always wins.
        if status == "SHORTLISTED":
            status = "INTERVIEW_SHORTLISTED"
            for item in evidence:
                if item.get("meaning") == "SHORTLISTED": item["meaning"] = status
        combined = " ".join(sources["EMAIL_SUBJECT"] + sources["EMAIL_BODY"])
        company, job, domain = _extract_context(subject, body, sender_email)
        conflict = "SHORTLISTED" in detected and status != "SHORTLISTED"
        if subject and any(token in subject.casefold() for token in ("congratulations", "next steps")):
            evidence.append({"source":"EMAIL_SUBJECT","meaning":status,"text":clean_email(subject)[:500]})
        return {
            "qualified": True, "score": max(0.94 if status == "JOINING_CONFIRMED" else 0.92, min(0.99, 0.9 + 0.02 * len(evidence))),
            "status": status, "evidence": evidence[:8], "ignore_reason": None,
            "joining_date": _extract_joining_date(combined) if status in {"JOINING_CONFIRMED", "POST_SELECTION_ONBOARDING", "JOINED"} else None,
            "company_name": company, "company_domain": domain, "job_title": job,
            "risk_flags": ["WORDING_STATUS_CONFLICT"] if conflict else [],
            "requires_manual_review": conflict,
        }
    semantic_status = semantic.get("interview_event") or semantic.get("lifecycle_event")
    if semantic_status and semantic_status != "NONE":
        # classify_context's assertive-context regexes (e.g. "your interview
        # ... is confirmed today") found a candidate-specific outcome that the
        # literal STATUS_SIGNALS phrase list did not match verbatim. Route it
        # to the model rather than silently dropping it as no-signal noise;
        # validate_lifecycle_event/validate_interview_event re-check
        # assertiveness independently before any status is ever accepted.
        return {
            "qualified": True, "score": 0.6, "status": semantic_status,
            "evidence": [{
                "source": "EMAIL_BODY", "meaning": semantic_status,
                "text": redact_sensitive_text(semantic["evidence_summary"]),
            }],
            "ignore_reason": None, "semantic_context": semantic,
        }
    haystack = " ".join([subject, sender_name, sender_email, body]).lower()
    for reason, phrases in NOISE_RULES:
        if any(phrase in haystack for phrase in phrases):
            return {"qualified": False, "score": 0.0, "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [], "ignore_reason": reason}
    return {"qualified": False, "score": 0.0, "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [], "ignore_reason": "NO_SELECTION_OR_OFFER_SIGNAL"}


_INVITE_STRUCTURE_CUES = (
    "microsoft teams meeting", "teams.microsoft.com", "meet.google.com",
    "zoom.us", "webex.com", "calendar invitation", "when:", "dtstart",
    "begin:vevent", "organiser:", "organizer:", "join the meeting",
    "join microsoft teams", "meeting id:", "add to calendar",
)
_ROLE_TITLE_CUES = (
    "engineer", "developer", "analyst", "architect", "consultant", "devops",
    "sre", "tester", "designer", "administrator", "specialist", "lead",
    "scientist", "programmer", "full stack", "fullstack", "backend",
    "frontend", "qa",
    # Stacks are named as the role in Indian recruiting subject lines:
    # "Interview schedule for Charan - ReactJS" names no "developer".
    "reactjs", "react", "angular", "node", "java", "python", ".net", "dotnet",
    "spring", "django", "aws", "azure",
)
# Wording that frames the meeting as being *about a person for a role*. This is
# the part an ordinary internal meeting does not have: "Discussion with Ramu
# about the budget" carries no role title, and a sprint invite carries neither.
_MEETING_ABOUT_PERSON_CUES = (
    "discussion with", "discussion for", "discussion regarding",
    "technical discussion", "call with", "screening", "screening round",
    "round with", "meeting with", "conversation with", "profile discussion",
    # "Interview schedule for <candidate>" frames the meeting around a person
    # just as "discussion with" does.
    "schedule for", "availability for", "interview for",
)


def recruiting_invite_signal(
    subject: str, body: str, sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> bool:
    """Does this look like a recruiting calendar invite that never says "interview"?

    Real invites arrive titled "Discussion with <candidate> for <role>" on a
    Teams/Meet link, and were dropped as NO_RECRUITMENT_ROUTING_SIGNAL because
    no keyword matched. Routing on "discussion" alone would pull in every
    internal meeting, so three independent structured signals are required
    together: a calendar/meeting invite structure, a job-role-like title, and
    wording framing the meeting around a person. Any one or two of those is not
    enough, so an ordinary business discussion still fails closed.
    """
    subject_text = str(subject or "").casefold()
    body_text = str(body or "").casefold()
    attachment_text = " ".join(
        str(item.get("text") or "") + " " + str(item.get("filename") or "")
        for item in (attachments or [])
    ).casefold()
    everything = " ".join((subject_text, body_text, attachment_text))

    has_invite_structure = (
        any(cue in everything for cue in _INVITE_STRUCTURE_CUES)
        or ".ics" in attachment_text
    )
    # The role must be named in the subject line: a signature block or a
    # footer mentioning "engineer" elsewhere is not what this is about.
    has_role_title = any(cue in subject_text for cue in _ROLE_TITLE_CUES)
    is_about_a_person = any(cue in subject_text for cue in _MEETING_ABOUT_PERSON_CUES)
    return bool(has_invite_structure and has_role_title and is_about_a_person)


def relevance_score(subject: str, body: str, filenames: list[str] | None = None, thread_context: list[dict[str, Any]] | None = None) -> float:
    # Filenames alone are intentionally excluded from qualification.
    return float(prefilter_decision(subject, body, thread_context=thread_context)["score"])


def routing_decision(
    subject: str,
    body: str,
    sender_name: str = "",
    sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
    thread_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decide whether an email needs semantic analysis, never its outcome.

    This intentionally uses rules only to discard obvious noise and messages
    with no recruitment signal. Ambiguous language such as "shortlisted" is
    always sent to the model because later sentences can confirm joining.
    """
    context = prefilter_decision(
        subject, body, sender_name, sender_email, attachments, thread_context
    )
    if context.get("qualified"):
        return {"send_to_ai": True, "score": context["score"], "reason": "POTENTIAL_OUTCOME", "context": context}
    # A catalogue of vacancies carries no outcome for this candidate, so there
    # is nothing for the model to decide. It reaches here because
    # `is_promotional_or_job_ad` did not recognise the digest, which is what let
    # a LinkedIn "Jobs that match your profile" mail through as
    # AMBIGUOUS_RECRUITMENT on cues like "position" and "candidate".
    if job_advertisement_digest(subject, body, sender_email, attachments):
        return {"send_to_ai": False, "score": 0.0, "reason": "JOB_RECOMMENDATION", "context": context}
    reason = str(context.get("ignore_reason") or "")
    if reason in {
        "JOB_RECOMMENDATION", "JOB_ALERT", "JOB_PORTAL_MARKETING",
        "PROFILE_NOTIFICATION", "ASSESSMENT", "INTERVIEW", "REJECTION",
    }:
        return {"send_to_ai": False, "score": 0.0, "reason": reason, "context": context}
    # The deterministic classifier already conclusively identified this
    # message as promotional/questionnaire/historical/question noise. Trust
    # that verdict outright: an incidental keyword match below (e.g. a job
    # portal's generic tips paragraph mentioning "recruiters") must never
    # override a conclusive semantic determination and force a model call.
    if context.get("semantic_context"):
        return {"send_to_ai": False, "score": 0.0, "reason": reason or "DETERMINISTIC_NOISE_FILTER", "context": context}
    combined = " ".join(
        [subject, body, sender_name, sender_email]
        + [str(item.get("text") or "") for item in (attachments or [])]
        + [" ".join(str(item.get(key) or "") for key in ("subject", "body")) for item in (thread_context or [])[-5:]]
    ).casefold()
    ambiguous_recruitment_cues = (
        "shortlist", "application", "selection", "selected", "offer", "joining",
        "onboarding", "appointment", "employment", "compensation", "recruiter",
        "candidate", "job role", "position", "background verification",
    )
    # Word-boundary match, not substring: a plain `cue in combined` check let
    # "offer" match inside "offering"/"offered" anywhere in the email (a bank
    # fraud-warning footer, a newsletter's "bond offering"), forcing routine
    # marketing noise through the model.
    if any(re.search(rf"\b{re.escape(cue)}\b", combined) for cue in ambiguous_recruitment_cues):
        return {"send_to_ai": True, "score": max(0.25, float(context.get("score") or 0)), "reason": "AMBIGUOUS_RECRUITMENT", "context": context}
    if recruiting_invite_signal(subject, body, sender_email, attachments):
        return {"send_to_ai": True, "score": max(0.3, float(context.get("score") or 0)), "reason": "RECRUITING_CALENDAR_INVITE", "context": context}
    return {"send_to_ai": False, "score": 0.0, "reason": "NO_RECRUITMENT_ROUTING_SIGNAL", "context": context}


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _evidence_supported(item: dict[str, Any], sources: dict[str, list[str]]) -> bool:
    needle = clean_email(str(item.get("text") or "")).casefold()
    return bool(needle) and any(needle in value.casefold() for value in sources.get(str(item.get("source") or ""), []))


# Unambiguous employer language, mapped to the meaning codes the routing gate
# recognises.
#
# The model is asked for a `meaning` code and returns a sentence instead - 950
# prose values against 55 coded ones in production - so genuine employer mail
# was dropped for want of a code it never produced. Rather than teach the gate
# to accept prose, which would also admit the marketing that phrases itself the
# same way, only these phrases are promoted.
#
# Kept deliberately short and specific. "Shortlisted" is absent because it is
# the single most common word in job-board marketing: "Shortlisted but your
# profile is incomplete", "Shortlisted for EMBA". A phrase earns a place here
# only if an aggregator would have no reason to send it.
_MEANING_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OFFER_LETTER_RECEIVED", (
        "offer letter has been sent", "offer letter has been released",
        "offer has been released", "released the offer",
        "offer letter is attached", "offer letter attached",
        "we are pleased to offer you",
    )),
    ("SELECTED", (
        "has been selected for the position", "has been selected for the role",
        "selected for the position of", "confirming your selection",
        "candidate has been selected",
    )),
    ("CANDIDATE_REJECTED", (
        "not matching the requirements", "profile not matching",
        "has been declined", "regret to inform", "we will not be moving forward",
    )),
    ("JOINING_CONFIRMED", (
        "joining has been confirmed", "expected to join on",
        "date of joining is", "confirmed your date of joining",
    )),
)


def normalise_evidence_meaning(item: dict[str, Any]) -> dict[str, Any]:
    """Promote unambiguous employer prose to a recognised meaning code.

    The original wording is kept in `meaning_text`, because the prose is the
    audit trail: it is what the model actually said about the mail, and a code
    derived from it is an interpretation.

    Anything that does not match stays exactly as it was, so the routing gate
    still refuses it. That is the point - this recovers genuine mail without
    becoming a general-purpose way around the gate.
    """
    meaning = str(item.get("meaning") or "").strip()
    if not meaning:
        return item
    if meaning.upper() in store.IMPORTANT_ALERT_EVIDENCE_MEANINGS:
        return item
    haystack = f"{meaning} {item.get('text') or ''}".casefold()
    for code, phrases in _MEANING_PHRASES:
        if any(phrase in haystack for phrase in phrases):
            promoted = dict(item)
            promoted["meaning_text"] = meaning
            promoted["meaning"] = code
            return promoted
    return item


_OFFER_FAMILY = {
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED",
    "OFFER_DECLINED", "OFFER_REVOKED", "COMPENSATION_CONFIRMATION",
}
_JOINING_FAMILY = {
    "JOINING_CONFIRMED", "JOINING_DATE_UPDATED", "JOINED",
    "POST_SELECTION_ONBOARDING",
}
_ASSERTIVE_EMPLOYMENT_LIFECYCLE = {
    "SELECTED", "FINAL_SELECTION_CONFIRMED", *_OFFER_FAMILY, *_JOINING_FAMILY,
}


def _needs_review_status(proposed: str) -> str | None:
    """The visible review status a distrusted offer/joining result becomes.

    Deliberately NOT members of OFFER_CASE_STATUSES: a review status must never
    feed offer-case, booking, acceptance or payment workflows. It exists only so
    the record stays auditable instead of being deleted.
    """
    status = str(proposed or "").upper()
    if status in _OFFER_FAMILY:
        return "OFFER_NEEDS_REVIEW"
    if status in _JOINING_FAMILY:
        return "JOINING_NEEDS_REVIEW"
    if status.startswith("INTERVIEW_"):
        return "INTERVIEW_PROPOSED"
    # Everything else the model may legitimately return — SELECTED,
    # FINAL_SELECTION_CONFIRMED, BACKGROUND_VERIFICATION, DOCUMENT_VERIFICATION,
    # CANDIDATE_REJECTED — was still collapsing to NONE and disappearing. A
    # rejection in particular must never vanish: the candidate outcome is the
    # whole point of the record.
    if status in TRACKED_STATUSES:
        return "SELECTION_NEEDS_REVIEW"
    return None


def validate_result(value: dict[str, Any], message: dict[str, Any] | None = None, attachments: list[dict[str, Any]] | None = None) -> None:
    from jsonschema import Draft202012Validator
    # Backward-compatible normalization for v1 responses while the configured
    # model transitions to the canonical lowercase classification contract.
    raw_confidence = float(value.get("confidence") or 0)
    if raw_confidence < 0 or raw_confidence > 100:
        raise ValueError("confidence must be between 0 and 100")
    if raw_confidence > 1:
        value["confidence"] = raw_confidence / 100.0
    context = classify_context(
        str((message or {}).get("subject") or ""),
        str((message or {}).get("body") or ""),
        sender_email=str((message or {}).get("sender_email") or ""),
        sent_at=(message or {}).get("sent_at"), attachments=attachments,
    )
    for key in (
        "email_intent", "document_type", "is_candidate_specific", "is_job_outcome",
        "is_current_event", "is_questionnaire", "is_promotional_or_job_ad",
        "is_historical_information", "historical_employment_evidence",
        "lifecycle_event", "evidence_summary",
        "business_domain", "interview_event",
    ):
        value.setdefault(key, context[key])
    value.setdefault("ai_status", "ANALYZED")
    value.setdefault("validation_status", "AI_DETECTED")
    value.setdefault("classification", store.canonical_classification(value))
    value.setdefault("candidate_status", store._CLASSIFICATION_STATUS[value["classification"]])
    value.setdefault("reason", str(value.get("ignore_reason") or "Contextual employment classification"))
    errors = list(Draft202012Validator(SCHEMA).iter_errors(value))
    if errors:
        raise ValueError("invalid selection/offer JSON: " + errors[0].message)
    # Second layer, in case such a mail reaches the model by another route. The
    # answer is left intact in the result so it stays auditable; it simply stops
    # being something the system tracks, which is the disposition a catalogue of
    # vacancies deserves however confidently it was read. Recorded the way every
    # other downgrade here is, and after schema validation because the schema
    # forbids the extra keys.
    if message is not None and job_advertisement_digest(
        str(message.get("subject") or ""), str(message.get("body") or ""),
        str(message.get("sender_email") or ""), attachments,
    ):
        value["downgraded_from"] = str(value.get("status") or "")
        value["downgrade_reason"] = "JOB_ADVERTISEMENT_DIGEST"
        value["is_selection_or_offer_related"] = False
        value["should_create_review_record"] = False
        value["requires_manual_review"] = False
        value["ignore_reason"] = "JOB_RECOMMENDATION"
        value["reason"] = "Job advertisement listing; no outcome for this candidate."
    confidence = float(value["confidence"])
    interview_statuses = {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"}
    proposed_status = str(value.get("status") or "").upper()
    assertive_interview_status = str(context.get("interview_event") or "NONE").upper()
    if proposed_status in interview_statuses or assertive_interview_status in interview_statuses:
        # Ollama can return a correct interview event and schedule while also
        # returning a contradictory generic workflow boolean or a speculative
        # lifecycle status such as JOINING_CONFIRMED. The model must not veto
        # or replace an assertive interview event recognized from the original
        # source text.
        safe_interview_status, _ = validate_interview_event(
            assertive_interview_status
            if assertive_interview_status in interview_statuses
            else proposed_status,
            context,
        )
        if safe_interview_status != "NONE":
            source_schedule = extract_interview_schedule(
                str((message or {}).get("subject") or ""),
                str((message or {}).get("body") or ""),
                sent_at=(message or {}).get("sent_at"),
            )
            interview = value.setdefault("interview", {})
            # Canonicalize a model-supplied 24-hour time only when it describes
            # the same minute as the explicit 12-hour source value. Missing or
            # conflicting model fields still fail the normal safety checks.
            model_time = str(interview.get("time") or "").strip()
            source_time = str(source_schedule.get("time") or "").strip()
            if model_time and source_time:
                try:
                    model_parsed = datetime.strptime(model_time, "%H:%M")
                    source_parsed = datetime.strptime(source_time.upper(), "%I:%M %p")
                    if (
                        model_parsed.hour * 60 + model_parsed.minute
                        == source_parsed.hour * 60 + source_parsed.minute
                    ):
                        interview["time"] = source_time
                except ValueError:
                    pass
            model_timezone = str(interview.get("timezone") or "").strip()
            source_timezone = str(source_schedule.get("timezone") or "").strip()
            if (
                model_timezone.upper() in {"IST", "ASIA/KOLKATA"}
                and source_timezone == "Asia/Kolkata"
            ):
                interview["timezone"] = source_timezone
            # The deterministic source parser reads an explicit visible range
            # from the email body. Preserve it even if a model omitted the end.
            source_end_time = str(source_schedule.get("end_time") or "").strip()
            if source_end_time:
                interview["end_time"] = source_end_time
                interview["duration_minutes"] = source_schedule.get("duration_minutes")
            classification = store._STATUS_CLASSIFICATION[safe_interview_status]
            value.update(
                status=safe_interview_status,
                classification=classification,
                candidate_status=store._CLASSIFICATION_STATUS[classification],
                is_selection_or_offer_related=True,
                should_create_review_record=True,
                is_job_outcome=True,
                is_current_event=True,
                business_domain="INTERVIEW_TRACKING",
                interview_event=safe_interview_status,
                lifecycle_event="NONE",
                ignore_reason=None,
            )
    positive = bool(value["is_selection_or_offer_related"] and value["should_create_review_record"] and value["status"] in TRACKED_STATUSES)
    if not positive:
        value["status"] = "IGNORED_NOT_OFFER_RELATED"
        value["should_create_review_record"] = False
        value["requires_manual_review"] = False
        value["ignore_reason"] = value.get("ignore_reason") or "AI_NOT_OFFER_RELATED"
        value["validation_status"] = "REJECTED"
        value["lifecycle_event"] = "NONE"
        return
    proposed_status = str(value.get("status") or "").upper()
    if value["status"] in interview_statuses:
        safe_status, rejection_reason = validate_interview_event(value["status"], context)
    else:
        safe_status, rejection_reason = validate_lifecycle_event(value["status"], context)
    # An independent validator can over-promote an offer letter to
    # JOINING_CONFIRMED merely because the letter states a future joining date.
    # Reconciliation historically selected that later stage; the safety layer
    # then rejected it and demoted the whole message to an untracked review
    # classification. Preserve the different positive stage that the original
    # source independently proves instead.
    supported_status = str(context.get("lifecycle_event") or "NONE").upper()
    if (
        safe_status == "NONE"
        and proposed_status in _ASSERTIVE_EMPLOYMENT_LIFECYCLE
        and supported_status in _ASSERTIVE_EMPLOYMENT_LIFECYCLE
    ):
        recovered_status, _ = validate_lifecycle_event(supported_status, context)
        if recovered_status != "NONE":
            safe_status = recovered_status
            classification = store._STATUS_CLASSIFICATION[safe_status]
            value.update(
                status=safe_status,
                classification=classification,
                candidate_status=store._CLASSIFICATION_STATUS[classification],
                normalised_from=proposed_status,
                normalisation_reason="ASSERTIVE_SOURCE_LIFECYCLE",
            )
            offer = value.setdefault("offer", {})
            if safe_status in _OFFER_FAMILY:
                offer["offer_detected"] = True
                offer["offer_letter_detected"] = safe_status == "OFFER_LETTER_RECEIVED"
                offer["appointment_letter_detected"] = safe_status == "APPOINTMENT_LETTER_RECEIVED"

            # Mail Alerts requires typed evidence when model disagreement keeps
            # manual review enabled. Add the deterministic source quote with its
            # canonical meaning while retaining the model evidence for audit.
            source_decision = prefilter_decision(
                str((message or {}).get("subject") or ""),
                str((message or {}).get("body") or ""),
                str((message or {}).get("sender_name") or ""),
                str((message or {}).get("sender_email") or ""),
                attachments,
                (message or {}).get("thread_context"),
            )
            typed_evidence = [
                item for item in source_decision.get("evidence") or []
                if str(item.get("meaning") or "").upper() == safe_status
            ]
            value["evidence"] = typed_evidence + list(value.get("evidence") or [])
            rejection_reason = None
    review_status_for = _needs_review_status(proposed_status)
    if safe_status == "NONE" and review_status_for:
        # Same rule as the interview downgrade: the backend may distrust an
        # offer or joining claim and withhold every side effect, but it must not
        # delete a valid classification. Confidence and verbatim evidence are
        # untouched; requires_manual_review keeps it out of any automated path.
        value.update(
            status=review_status_for,
            classification=review_status_for.lower(),
            candidate_status=("Offer — needs review"
                              if review_status_for == "OFFER_NEEDS_REVIEW"
                              else "Joining — needs review"
                              if review_status_for == "JOINING_NEEDS_REVIEW"
                              else "Interview — needs review"
                              if review_status_for == "INTERVIEW_PROPOSED"
                              else "Selection — needs review"),
            is_selection_or_offer_related=True,
            should_create_review_record=True,
            requires_manual_review=True,
            lifecycle_event=review_status_for,
            validation_status="NEEDS_REVIEW",
            downgraded_from=proposed_status,
            downgrade_reason=rejection_reason or "OUTCOME_NOT_ASSERTED",
        )
        value.pop("ignore_reason", None)
        return
    if safe_status == "NONE" and proposed_status in interview_statuses:
        # A backend semantic check may distrust the *booking detail* of an
        # interview, but it must not delete a valid classification. Ollama is
        # the authority on meaning; this layer is the authority on booking
        # safety. Downgrading keeps the event, the evidence and the confidence
        # visible for a human, while auto-booking stays gated downstream.
        value.update(
            status="INTERVIEW_PROPOSED",
            classification="interview_proposed",
            candidate_status="Interview Proposed",
            is_selection_or_offer_related=True,
            should_create_review_record=True,
            requires_manual_review=True,
            lifecycle_event="INTERVIEW_PROPOSED",
            validation_status="NEEDS_REVIEW",
            downgraded_from=proposed_status,
            downgrade_reason=rejection_reason or "INTERVIEW_DETAIL_NOT_ASSERTIVE",
        )
        value.pop("ignore_reason", None)
        return
    if safe_status == "NONE":
        value.update(
            status="IGNORED_NOT_OFFER_RELATED",
            classification="not_relevant",
            candidate_status="Profile Active",
            is_selection_or_offer_related=False,
            should_create_review_record=False,
            requires_manual_review=False,
            ignore_reason=rejection_reason or context["email_intent"],
            validation_status="REJECTED",
            lifecycle_event="NONE",
            is_job_outcome=False,
            evidence_summary=context["evidence_summary"],
            summary=context["evidence_summary"],
        )
        return
    is_interview_event = safe_status in interview_statuses
    value["lifecycle_event"] = "NONE" if is_interview_event else safe_status
    value["interview_event"] = safe_status if is_interview_event else "NONE"
    value["business_domain"] = "INTERVIEW_TRACKING" if is_interview_event else "SELECTION_TRACKING"
    value["email_intent"] = context["email_intent"]
    value["document_type"] = context["document_type"]
    value["is_job_outcome"] = True
    value["is_current_event"] = True
    value["evidence_summary"] = context["evidence_summary"]
    value["evidence"] = [
        {**item, "text": redact_sensitive_text(str(item.get("text") or ""))}
        for item in value.get("evidence") or []
    ]
    sources = _source_texts((message or {}).get("subject", ""), (message or {}).get("body", ""), attachments, (message or {}).get("thread_context"))
    # Keep only evidence that is verbatim in the source, and require at least
    # one such item — rather than discarding the whole classification because a
    # single item was paraphrased. The verbatim test is what protects against
    # invented evidence, and it still applies to everything that survives:
    # unsupported items are dropped, never trusted. Rejecting outright threw
    # away a correct OFFER_IN_PROGRESS at 95% whose body quote was verbatim,
    # purely because a second item was a paraphrase.
    supported = [item for item in value["evidence"] if _evidence_supported(item, sources)]
    if not supported:
        raise ValueError("selection/offer evidence is missing or unsupported")
    # Promote unambiguous employer phrasing to a meaning code before the value
    # is persisted, so the routing gate sees a code without being loosened.
    value["evidence"] = [normalise_evidence_meaning(item) for item in supported]
    auto_threshold = max(0.8, min(0.99, float(os.getenv("AI_RECRUITMENT_AUTO_ACCEPT_THRESHOLD", "0.90"))))
    review_threshold = max(0.0, min(1.0, float(os.getenv("OLLAMA_CONFIDENCE_THRESHOLD", "0.75"))))
    if confidence < review_threshold:
        value.update(status="MANUAL_REVIEW_REQUIRED", classification="needs_review", candidate_status="Needs Review",
                     should_create_review_record=True, requires_manual_review=True, ignore_reason=None,
                     reason="AI confidence is below the configured automatic-update threshold")
        value["validation_status"] = "NEEDS_REVIEW"
    elif confidence < auto_threshold:
        actionable_interview = value.get("classification") in {"interview_confirmed", "interview_rescheduled"}
        explicit_schedule = all(str((value.get("interview") or {}).get(key) or "").strip() for key in ("date", "time", "timezone"))
        if not (actionable_interview and explicit_schedule and not value.get("risk_flags")):
            value.update(status="MANUAL_REVIEW_REQUIRED", requires_manual_review=True, ignore_reason=None)
        value["validation_status"] = "NEEDS_REVIEW"
    else:
        value["requires_manual_review"] = bool(value.get("requires_manual_review") or value.get("risk_flags"))
        value["ignore_reason"] = None
        value["validation_status"] = "NEEDS_REVIEW" if value["requires_manual_review"] else "AUTO_VALIDATED"
    for field in ("offer_date", "joining_date", "offer_expiry_date"):
        raw = (value.get("offer") or {}).get(field)
        if raw:
            try:
                date.fromisoformat(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid ISO date: offer.{field}") from exc
    if value.get("classification") in {"interview_confirmed", "interview_rescheduled"}:
        interview = value.get("interview") or {}
        date_valid = True
        time_valid = True
        tz_valid = True
        normalised_date = _normalise_interview_date(interview.get("date"))
        if normalised_date:
            interview["date"] = normalised_date
            value["interview"] = interview
        else:
            date_valid = False
        normalised_time = _normalise_interview_time(interview.get("time"))
        if not normalised_time:
            # The model sometimes reformats a clearly-stated AM/PM time into
            # 24-hour ("14:00 - 15:00") while quoting the source's own
            # "2:00 PM - 3:00 PM IST" verbatim in its evidence. Recover the
            # stated time from the subject or from evidence quotes, which the
            # check above already verified appear verbatim in the source.
            # Nothing is invented: a source with no AM/PM anywhere still fails,
            # so a bare 17:00 is rejected exactly as before.
            source_texts = [str((message or {}).get("subject") or "")] + [
                str(item.get("text") or "") for item in value.get("evidence") or []
            ]
            for candidate_text in source_texts:
                recovered = _normalise_interview_time(candidate_text)
                if recovered:
                    normalised_time = recovered
                    break
            if not normalised_time:
                # Some recruiters simply write the schedule on a 24-hour clock:
                # EY sent "Time: 16:30 to 17:30" and Accenture "Time: 12:00:00
                # until 13:00:00 IST (24 Hours)". Requiring AM/PM used the
                # meridiem as a proxy for "the source really stated a time",
                # which is too strict for an hour that has only one possible
                # reading, so both interviews looped until they were parked.
                # Recovery still reads only source-verified text, so a source
                # with no clock time at all recovers nothing, and an ambiguous
                # bare 1-12 is still refused.
                normalised_time = _recover_unambiguous_24_hour_time(source_texts)
        if normalised_time:
            interview["time"] = normalised_time
            value["interview"] = interview
        else:
            time_valid = False
        if not str(interview.get("timezone") or "").strip():
            tz_valid = False
        if not (date_valid and time_valid and tz_valid):
            missing = []
            if not date_valid: missing.append("date")
            if not time_valid: missing.append("time")
            if not tz_valid: missing.append("timezone")
            labels={"date":"ISO date","time":"12-hour time","timezone":"timezone"}
            raise ValueError("interview requires valid " + ", ".join(labels[item] for item in missing))


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# "20-Jul-2026" / "06th July 2026" / "20 July, 2026"
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:ST|ND|RD|TH)?[\s\-/,]+([A-Z]{3,9})[\s\-/,]+(\d{4})\b", re.I,
)
# "Jul 20, 2026" / "July 20 2026"
_MONTH_DAY_YEAR = re.compile(
    r"\b([A-Z]{3,9})[\s\-/,]+(\d{1,2})(?:ST|ND|RD|TH)?[\s\-/,]+(\d{4})\b", re.I,
)


def _normalise_interview_date(raw):
    """Canonicalise an interview date to ISO, or "" if it is not a real date.

    `date.fromisoformat` alone rejected values that are unambiguous dates in
    every other respect: ValueMomentum sent "20-Jul-2026" and Cangra sent the
    full ISO timestamp "2026-07-30T12:00:00+05:30". Both looped on
    OLLAMA_SCHEMA_VALIDATION_FAILED until they were parked, so the interviews
    never surfaced.

    Only spellings with a named month or an explicit ISO form are accepted.
    All-numeric forms like "07/08/2026" stay rejected because day-first and
    month-first cannot be told apart, and guessing one would book the wrong day.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    # A full ISO timestamp carries the date unambiguously in its first token.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for pattern, order in ((_DAY_MONTH_YEAR, "dmy"), (_MONTH_DAY_YEAR, "mdy")):
        hit = pattern.search(text)
        if not hit:
            continue
        first, second, year = hit.group(1), hit.group(2), hit.group(3)
        day_part, month_name = (first, second) if order == "dmy" else (second, first)
        month = _MONTH_NAMES.get(str(month_name).lower())
        if not month:
            continue
        try:
            return date(int(year), month, int(day_part)).isoformat()
        except ValueError:
            return ""
    return ""


# A 24-hour clock reading of 13:00 or later, or 00:xx, has exactly one meaning.
# Only a colon separates the parts: "18.30" is far more often money or a version
# than a time, and a false time is worse than an unread one.
_TWENTY_FOUR_HOUR = re.compile(r"(?<![:.\d])([01]?\d|2[0-3]):([0-5]\d)(?!:?\d*\s*(?:AM|PM))", re.I)


def _recover_unambiguous_24_hour_time(source_texts) -> str:
    """The stated start time when a source writes it on a 24-hour clock.

    Reads only text already verified to appear verbatim in the source, so this
    can never invent a time the sender did not write. An hour of 13-23 (or 00)
    has a single possible reading and is taken as stated. An hour of 1-12 is
    ambiguous on its own and is only accepted when the same passage also states
    an hour of 13 or more, which proves the passage is on a 24-hour clock --
    Accenture's "12:00:00 until 13:00:00" is exactly that shape.
    """
    for text in source_texts:
        found = [
            (int(hit.group(1)), hit.group(2))
            for hit in _TWENTY_FOUR_HOUR.finditer(str(text or ""))
        ]
        if not found:
            continue
        passage_is_24_hour = any(hour >= 13 for hour, _minute in found)
        for hour, minute in found:
            if hour >= 13:
                return "%02d:%s PM" % (hour - 12, minute)
            if hour == 0:
                return "12:%s AM" % minute
            if passage_is_24_hour:
                # 1-11 are morning on a 24-hour clock; 12 is noon.
                return "%02d:%s %s" % (hour, minute, "PM" if hour == 12 else "AM")
    return ""


def _normalise_interview_time(raw):
    """Canonicalise a 12-hour time's formatting, or "" if it is not one.

    The validator demanded exactly "H:MM AM/PM", so a real invite reading
    "2:00 PM - 3:00 PM IST" failed and raised OLLAMA_SCHEMA_VALIDATION_FAILED
    on every retry - identical input, identical failure - so the interview
    never surfaced. A range, an attached zone and spacing are formatting, not
    evidence, so the first stated 12-hour time is taken as the start.

    A 24-hour time is deliberately NOT accepted: the prompt requires an
    explicit AM/PM as evidence the source stated the time unambiguously, and
    an existing test pins that. Forgiving formatting must not forgive
    missing evidence.
    """
    text = str(raw or "").upper()
    hit = re.search(r"(\d{1,2})[:.]([0-5]\d)\s*(AM|PM)", text)
    if hit:
        hour = int(hit.group(1))
        return "%02d:%s %s" % (hour, hit.group(2), hit.group(3)) if 1 <= hour <= 12 else ""
    hit = re.search(r"(?<![:.\d])(\d{1,2})\s*(AM|PM)", text)
    if hit:
        hour = int(hit.group(1))
        return "%02d:00 %s" % (hour, hit.group(2)) if 1 <= hour <= 12 else ""
    return ""


def parse_model_json(raw: str) -> dict[str, Any]:
    value = (raw or "").strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        value = re.sub(r"^```(?:json)?|```$", "", value, flags=re.I | re.M).strip()
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI output did not contain a JSON object")
        return json.loads(re.sub(r",\s*([}\]])", r"\1", value[start:end + 1]))


CLASSIFIER_PROMPT = """You are TeleAutomation recruitment_email_status_extraction_v3.
Analyze the complete business meaning of the subject, cleaned body, sender, recipient,
thread context, and extracted attachment text. Do not classify from a single word.
Choose the furthest stage that the complete evidence actually confirms.

Track employment outcomes and material status updates: SELECTED,
FINAL_SELECTION_CONFIRMED, OFFER_INDICATION, OFFER_IN_PROGRESS, OFFER_APPROVED,
OFFER_LETTER_RECEIVED, APPOINTMENT_LETTER_RECEIVED, OFFER_ACCEPTED,
OFFER_DECLINED, OFFER_REVOKED, JOINING_CONFIRMED, JOINING_DATE_UPDATED,
POST_SELECTION_ONBOARDING, BACKGROUND_VERIFICATION, DOCUMENT_VERIFICATION,
COMPENSATION_CONFIRMATION, INTERVIEW_UPDATE, INTERVIEW_SHORTLISTED,
INTERVIEW_CONFIRMED, INTERVIEW_RESCHEDULED, INTERVIEW_CANCELLED,
CANDIDATE_REJECTED, or JOINED. A specific confirmed joining date and post-selection
logistics can establish JOINING_CONFIRMED even when the same email says
"shortlisted". In that case add WORDING_STATUS_CONFLICT.

Do not track job recommendations, alerts, profile matches, invitations to apply,
incomplete applications, recruiter introductions, application acknowledgements,
marketing, or other non-candidate-specific activity. Interview and rejection mail
must use their explicit informational classifications, not offer classifications.

Use INTERVIEW_CONFIRMED only when a candidate-specific interview has an explicit
date, 12-hour AM/PM time, and timezone. Use INTERVIEW_RESCHEDULED only when the
message clearly changes an existing interview and includes the new schedule. Use
INTERVIEW_CANCELLED only for an explicit cancellation. A shortlist without a
schedule is INTERVIEW_SHORTLISTED or INTERVIEW_UPDATE and must never be confirmed.
When an explicit interview end time or duration is present, return it as
interview.end_time or interview.duration_minutes. Never replace a visible duration
with a default value.
For a reschedule or cancellation, preserve any stated prior schedule in
interview.original_date, interview.original_time, and interview.original_timezone.
Return IST as Asia/Kolkata. Never invent schedule, round, company, or meeting link.
First classify email_intent, document_type, business_domain, lifecycle_event, and
interview_event. Questions, requested fields,
questionnaires, job advertisements, payslips, and historical employment documents
must return lifecycle_event NONE and is_job_outcome false. JOINING_CONFIRMED means
a confirmed joining arrangement; JOINED requires an explicit statement that work
actually started. Return confidence as 0-100; the backend normalizes it after parsing.

Requested actions and conditional wording matter. "Complete your application to
move forward" is application-stage, not selection. Evidence must be short verbatim
text present in EMAIL_SUBJECT, EMAIL_BODY, ATTACHMENT, or THREAD_CONTEXT.
Also return the canonical lowercase classification, user-facing candidate_status,
and a concise evidence_summary/reason. Never include bank, PAN, Aadhaar, UAN, PF,
or other financial/government identifiers. Return only JSON matching
selection_offer_event_v1."""

VALIDATOR_PROMPT = """You are the independent validator for a high-impact employment
outcome classifier. Re-read the complete source independently, then inspect the
primary model result. Correct false positives and false negatives. In particular,
shortlist plus an incomplete-application request is not an outcome, while shortlist
plus a confirmed joining date and post-selection logistics is JOINING_CONFIRMED.
Return a complete selection_offer_event_v1 JSON result supported by verbatim source
evidence. Do not defer to the primary result merely because it is present."""


def _analysis_payload(message: dict[str, Any], attachment_texts: list[dict[str, str]] | None) -> dict[str, Any]:
    return {
        "subject": message.get("subject"), "sender_name": message.get("sender_name"),
        "sender_email": message.get("sender_email"), "recipient": message.get("recipient_email"),
        "email_date": str(message.get("sent_at")), "body": clean_email(message.get("body") or ""),
        "thread_context": (message.get("thread_context") or [])[-5:],
        "attachments": attachment_texts or [],
    }


def _manual_review_from_strong_context(
    message: dict[str, Any], routing_context: dict[str, Any], failure: Exception | None = None
) -> dict[str, Any] | None:
    """Preserve a strongly evidenced outcome when local AI is unavailable.

    This never auto-verifies or mutates a candidate.  It only creates a
    pending administrator review record when the deterministic semantic
    router already found a tracked outcome with quoted source evidence.
    """
    status = str(routing_context.get("status") or "")
    evidence = list(routing_context.get("evidence") or [])
    fallback_statuses = {
        "SELECTED", "FINAL_SELECTION_CONFIRMED", "FINAL_ROUND_CLEARED", "OFFER_INDICATION",
        "OFFER_IN_PROGRESS", "OFFER_APPROVED", "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED",
        "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED", "JOINING_CONFIRMED",
        "JOINED", "POST_SELECTION_ONBOARDING", "BACKGROUND_VERIFICATION",
        "DOCUMENT_VERIFICATION", "HR_CONFIRMATION", "COMPENSATION_CONFIRMATION",
        "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED", "INTERVIEW_SHORTLISTED",
    }
    if not routing_context.get("qualified") or status not in fallback_statuses or not evidence:
        return None
    confidence = min(0.89, max(0.80, float(routing_context.get("score") or 0.80)))
    failure_code = getattr(failure, "code", None) or "OLLAMA_INTERNAL_ERROR"
    fallback_reason = str(failure) if failure else "Local AI validation did not complete."
    offer_statuses = {
        "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
        "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED",
        "OFFER_ACCEPTED", "JOINING_CONFIRMED", "JOINED",
        "POST_SELECTION_ONBOARDING",
    }
    is_interview = status in {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"}
    interview = (
        extract_interview_schedule(
            str(message.get("subject") or ""), str(message.get("body") or ""),
            sent_at=message.get("sent_at"),
        ) if is_interview else
        {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")}
    )
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": status,
        "primary_status": status,
        "confidence": confidence,
        "ignore_reason": None,
        "candidate": {"name": None, "email": message.get("recipient_email")},
        "company": {
            "name": routing_context.get("company_name"),
            "domain": routing_context.get("company_domain"),
        },
        "job": {
            "title": routing_context.get("job_title"),
            "employment_type": None,
            "location": None,
        },
        "recruiter": {
            "name": message.get("sender_name"),
            "email": message.get("sender_email"),
        },
        "interview": interview,
        "offer": {
            "offer_detected": status in offer_statuses,
            "offer_letter_detected": status == "OFFER_LETTER_RECEIVED",
            "appointment_letter_detected": status == "APPOINTMENT_LETTER_RECEIVED",
            "offer_date": None,
            "offered_ctc": None,
            "currency": None,
            "joining_date": routing_context.get("joining_date"),
            "offer_expiry_date": None,
        },
        "attachments": [],
        "evidence": evidence[:8],
        "risk_flags": list(dict.fromkeys(
            list(routing_context.get("risk_flags") or []) + ["AI_UNAVAILABLE_MANUAL_REVIEW"]
        )),
        "requires_manual_review": True,
        "manual_review_required": True,
        "classification_source": "FALLBACK",
        "ai_validation_status": "UNAVAILABLE",
        "ai_status": "UNAVAILABLE",
        "validation_status": "NEEDS_REVIEW",
        "lifecycle_event": "NONE",
        "interview_event": status if is_interview else "NONE",
        "business_domain": "INTERVIEW_TRACKING" if is_interview else "SELECTION_TRACKING",
        "fallback_reason": failure_code,
        "fallback_confidence": confidence,
        "summary": (
            f"Fallback evidence indicates {status.replace('_', ' ').lower()}. "
            f"AI validation unavailable ({failure_code}); administrator review is required."
        ),
        "ai_diagnostic_message": fallback_reason,
        "recommended_action": "Administrator must verify the source email before confirming this outcome.",
    }


def _requires_independent_validation(result: dict[str, Any], routing_context: dict[str, Any] | None = None) -> bool:
    threshold = float(os.getenv("AI_RECRUITMENT_AUTO_ACCEPT_THRESHOLD", "0.90"))
    confidence = float(result.get("confidence") or 0)
    if confidence > 1:
        confidence /= 100.0
    flags = {str(flag).upper() for flag in result.get("risk_flags") or []}
    return (
        confidence < threshold
        or bool(flags)
        or bool(result.get("requires_manual_review"))
        or (result.get("is_selection_or_offer_related") is True and result.get("status") in TRACKED_STATUSES)
        or bool((routing_context or {}).get("qualified"))
        or bool((routing_context or {}).get("risk_flags"))
    )


def _reconcile_model_results(primary: dict[str, Any], validator: dict[str, Any]) -> dict[str, Any]:
    primary_confidence = float(primary.get("confidence") or 0)
    validator_confidence = float(validator.get("confidence") or 0)
    if primary_confidence > 1: primary_confidence /= 100.0
    if validator_confidence > 1: validator_confidence /= 100.0
    primary_positive = bool(primary.get("is_selection_or_offer_related") and primary.get("status") in TRACKED_STATUSES)
    validator_positive = bool(validator.get("is_selection_or_offer_related") and validator.get("status") in TRACKED_STATUSES)
    same = primary_positive == validator_positive and primary.get("status") == validator.get("status")
    if same:
        chosen = dict(validator)
        chosen["confidence"] = min(primary_confidence, validator_confidence)
        chosen["model_validation"] = {"agreed": True, "primary_status": primary.get("status"), "validator_status": validator.get("status")}
        return chosen
    if not primary_positive and not validator_positive:
        chosen = dict(validator)
        chosen["model_validation"] = {"agreed": False, "primary_status": primary.get("status"), "validator_status": validator.get("status")}
        return chosen
    if validator_positive:
        # The independent validator is specifically used to recover semantic
        # false negatives and resolve difficult stage wording. Keep its exact
        # outcome, but require administrator review when the models disagree.
        chosen = dict(validator)
        chosen["requires_manual_review"] = True
        chosen["risk_flags"] = list(dict.fromkeys((chosen.get("risk_flags") or []) + ["MODEL_DISAGREEMENT"]))
        chosen["model_validation"] = {"agreed": False, "primary_status": primary.get("status"), "validator_status": validator.get("status")}
        return chosen
    positive = validator if validator_positive else primary
    chosen = dict(positive)
    chosen["status"] = "MANUAL_REVIEW_REQUIRED"
    chosen["is_recruitment_related"] = True
    chosen["is_selection_or_offer_related"] = True
    chosen["should_create_review_record"] = True
    chosen["requires_manual_review"] = True
    positive_confidence = float(positive.get("confidence") or 0)
    if positive_confidence > 1: positive_confidence /= 100.0
    chosen["confidence"] = min(positive_confidence, 0.89)
    chosen["risk_flags"] = list(dict.fromkeys((chosen.get("risk_flags") or []) + ["MODEL_DISAGREEMENT"]))
    chosen["model_validation"] = {"agreed": False, "primary_status": primary.get("status"), "validator_status": validator.get("status")}
    return chosen


def _prompt_json(value: Any) -> str:
    """Serialize provider timestamps and other scalar metadata for model prompts."""
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item),
    )


_SHORTLIST_CANDIDATE_STATUSES = {
    "interview shortlisted", "shortlisted", "candidate shortlisted",
    "profile shortlisted",
}
# Wording that states the shortlist outcome. A label alone is not enough: the
# mail itself has to say it, so a generic document request can never be
# promoted no matter what the model puts in candidate_status.
_SHORTLIST_EVIDENCE_PHRASES = (
    "provisionally shortlisted", "profile is shortlisted",
    "profile has been shortlisted", "profile is provisionally shortlisted",
    "you have been shortlisted", "you are shortlisted",
    "we have shortlisted your", "shortlisted for the role",
    "shortlisted for the position", "candidature has been shortlisted",
    "candidature has been provisionally shortlisted",
    "shortlisted for further discussion", "shortlisted for hr discussion",
)
# Only these are promotable. A confirmed/cancelled interview, an offer or a
# rejection already carries a stronger outcome and must never be overwritten.
_SHORTLIST_PROMOTABLE_STATUSES = {"MANUAL_REVIEW_REQUIRED", "INTERVIEW_UPDATE"}


def normalise_shortlist_status(value: dict[str, Any], message: dict[str, Any] | None = None) -> bool:
    """Map a model result that plainly describes a shortlist onto the canonical status.

    The model reads these mails correctly — it returned candidate_status
    "Interview Shortlisted", is_selection_or_offer_related true and a reason
    naming the provisional shortlist — but parked the result at
    MANUAL_REVIEW_REQUIRED/INTERVIEW_UPDATE because no interview slot was
    offered. Shortlisting is the outcome; the document list is the next action.

    Runs after the schema guard, on the validated result, so the guard still
    sees exactly what the model produced. Returns whether it promoted anything.
    """
    status = str(value.get("status") or "").upper()
    if status not in _SHORTLIST_PROMOTABLE_STATUSES:
        return False
    if not value.get("is_selection_or_offer_related"):
        return False

    label = str(value.get("candidate_status") or "").strip().lower()
    # Evidence must come from the mail itself, never from the model's own prose:
    # a summary that says "shortlisted" about a bare document request would
    # otherwise promote it on the strength of the model's wording alone.
    haystack = " ".join(str(part or "").lower() for part in (
        (message or {}).get("subject"), (message or {}).get("body"),
    ))
    stated = any(phrase in haystack for phrase in _SHORTLIST_EVIDENCE_PHRASES)
    # Both the model's own label and the wording in the mail must agree, so a
    # merely ambiguous recruitment update stays in manual review.
    if label not in _SHORTLIST_CANDIDATE_STATUSES or not stated:
        return False

    value["status"] = "INTERVIEW_SHORTLISTED"
    value["is_selection_or_offer_related"] = True
    value["should_create_review_record"] = True
    value["requires_manual_review"] = False
    value["shortlist_normalised_from"] = status
    return True


def analyze(message: dict[str, Any], attachment_texts: list[dict[str, str]] | None = None) -> tuple[dict[str, Any], str, int]:
    payload = _analysis_payload(message, attachment_texts)
    routing_context = routing_decision(
        message.get("subject", ""), message.get("body", ""),
        message.get("sender_name", ""), message.get("sender_email", ""),
        attachment_texts, message.get("thread_context"),
    ).get("context") or {}
    models = configured_models()
    deadline = time.monotonic() + max(
        20.0,
        float(os.getenv("AI_JOB_TIMEOUT", os.getenv("AI_RECRUITMENT_JOB_TIMEOUT_SECONDS", "660"))),
    )

    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise AIGatewayError("Recruitment AI job deadline exceeded.", code="OLLAMA_REQUEST_TIMEOUT")
        request_limit = max(
            5.0,
            float(os.getenv("OLLAMA_REQUEST_TIMEOUT", os.getenv("AI_RECRUITMENT_REQUEST_TIMEOUT_SECONDS", "300"))),
        )
        return min(request_limit, remaining)

    def request_model(
        *, messages: list[dict[str, Any]], model: str,
        max_retries: int | None = None, allow_fallback: bool = True,
        workload: str = "recruitment_mail_classification",
    ):
        """Use the lightweight fallback when the configured runner cannot serve."""
        try:
            return chat_structured(
                messages=messages, schema=SCHEMA, model=model, max_retries=max_retries,
                timeout=remaining_timeout(),
                deadline_monotonic=deadline,
                workload=workload,
            )
        except AIGatewayError as exc:
            fallback = str(models.get("fallback") or "").strip()
            eligible = {
                "OLLAMA_INTERNAL_ERROR", "OLLAMA_MODEL_LOAD_FAILED",
                "OLLAMA_REQUEST_TIMEOUT",
            }
            if not allow_fallback or not fallback or fallback == model or exc.code not in eligible:
                raise
            logger.warning(
                "Recruitment model failed; retrying fallback primary=%s fallback=%s code=%s",
                model, fallback, exc.code,
            )
            return chat_structured(
                messages=messages, schema=SCHEMA, model=fallback, max_retries=0,
                timeout=remaining_timeout(),
                deadline_monotonic=deadline,
                workload=f"{workload}_fallback",
            )

    try:
        primary_response = request_model(
            messages=[{"role": "system", "content": CLASSIFIER_PROMPT}, {"role": "user", "content": _prompt_json(payload)}],
            model=models["primary"],
            max_retries=0,
            workload="recruitment_mail_primary",
        )
        try:
            primary = parse_model_json(primary_response.content)
        except (ValueError, json.JSONDecodeError):
            # One bounded repair retry with an explicit JSON-only instruction.
            repair_response = request_model(
                messages=[{"role": "system", "content": CLASSIFIER_PROMPT + " Return valid JSON only; no markdown or commentary."},
                          {"role": "user", "content": _prompt_json(payload)}],
                model=primary_response.model, max_retries=0,
                workload="recruitment_mail_primary_json_repair",
            )
            try:
                primary = parse_model_json(repair_response.content)
                primary_response = repair_response
            except (ValueError, json.JSONDecodeError) as exc:
                raise AIGatewayError("Ollama returned malformed classification JSON after one repair retry.", code="OLLAMA_INVALID_JSON") from exc
        logger.info("Ollama response JSON extracted for recruitment classification")
        result = primary
        model_label = primary_response.model
        duration = primary_response.duration_ms
        if _requires_independent_validation(primary, routing_context):
            validator_response = request_model(
                messages=[{"role": "system", "content": VALIDATOR_PROMPT}, {"role": "user", "content": _prompt_json({"source": payload, "primary_result": primary})}],
                model=models["validator"],
                max_retries=0,
                allow_fallback=False,
                workload="recruitment_mail_validator",
            )
            try:
                validator = parse_model_json(validator_response.content)
            except (ValueError, json.JSONDecodeError):
                validator_response = request_model(
                    messages=[{"role": "system", "content": VALIDATOR_PROMPT + " Return valid JSON only; no markdown or commentary."},
                              {"role": "user", "content": _prompt_json({"source": payload, "primary_result": primary})}],
                    model=validator_response.model, max_retries=0, allow_fallback=False,
                    workload="recruitment_mail_validator_json_repair",
                )
                try:
                    validator = parse_model_json(validator_response.content)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise AIGatewayError("Ollama validator returned malformed JSON after one repair retry.", code="OLLAMA_INVALID_JSON") from exc
            result = _reconcile_model_results(primary, validator)
            model_label = f"{primary_response.model}|validator:{validator_response.model}"
            duration += validator_response.duration_ms
        try:
            validate_result(result, message, attachment_texts)
        except ValueError as exc:
            raise AIGatewayError(f"Ollama response failed schema validation: {exc}", code="OLLAMA_SCHEMA_VALIDATION_FAILED") from exc
        logger.info("Ollama recruitment response schema validated")
        normalise_shortlist_status(result, message)
        result["primary_status"] = result["status"]
        result["classification_source"] = "OLLAMA"
        result["ai_validation_status"] = "VALIDATED"
        return result, model_label, duration
    except AIGatewayError:
        raise
    except Exception as exc:
        logger.exception("Recruitment AI analysis failed with unexpected error type=%s message=%s", type(exc).__name__, str(exc))
        raise AIGatewayError("AI semantic analysis failed.", code="OLLAMA_INTERNAL_ERROR") from exc


# A refusal by our own schema check is a statement about the model's answer, not
# about the service being reachable, so it does not belong on the infrastructure
# retry path.
_DETERMINISTIC_AI_FAILURES = frozenset({"OLLAMA_SCHEMA_VALIDATION_FAILED"})

# Sampling can turn a rejected answer into a valid one, so a couple of genuine
# attempts run first. Beyond that the loop is only spending inference to be told
# the same thing again.
_VALIDATION_RETRY_ALLOWANCE = 2


def process_message(mailbox: dict[str, Any], decoded: dict[str, Any], attachment_texts: list[dict[str, str]] | None = None, *, reprocess: bool = False, defer_ai: bool = False) -> dict[str, Any] | None:
    from services.mail_attachment_processor import extract_attachment
    decoded["body"] = clean_email(decoded.get("body") or "")
    decoded["message_hash"] = content_hash("|".join([decoded.get("sender_email") or "", decoded.get("subject") or "", str(decoded.get("sent_at"))]))
    processed = [extract_attachment(item) if item.get("data") is not None else item for item in (attachment_texts or [])]
    safe = [{key: item.get(key) for key in ("filename", "mime_type", "text", "attachment_type", "extraction_status", "checksum")} for item in processed]
    decoded["attachments"] = safe
    # Attachments are part of what makes a message distinct. A calendar
    # organiser who moves a meeting re-sends the identical covering note with a
    # new invite.ics; hashing the body alone made that revision look like a
    # resend and dropped it before anything read the new time.
    fingerprint = "|".join(sorted(
        str(item.get("checksum") or "") for item in safe if item.get("checksum")
    ))
    decoded["body_hash"] = content_hash(
        decoded["body"] + ("#attachments:" + fingerprint if fingerprint else "")
    )
    critical_attachment_failure = any(
        str(item.get("attachment_type") or "") in {"OFFER_LETTER","APPOINTMENT_LETTER","JOINING_LETTER","COMPENSATION_BREAKUP"}
        and str(item.get("extraction_status") or "") in {"FAILED","MANUAL_REVIEW_REQUIRED"}
        for item in safe
    )
    route = routing_decision(decoded.get("subject", ""), decoded["body"], decoded.get("sender_name", ""), decoded.get("sender_email", ""), safe, decoded.get("thread_context"))
    if critical_attachment_failure and not route["send_to_ai"]:
        route = {"send_to_ai":True,"score":0.25,"reason":"CRITICAL_ATTACHMENT_EXTRACTION_FAILED","context":route.get("context") or {}}
    row, created = store.insert_message(mailbox, decoded, float(route["score"]))
    _publish("mail_received", candidate_id=mailbox.get("candidate_id"), gmail_message_id=decoded.get("provider_message_id"), processing_status="Email Received")
    # A crash can happen after the durable message insert but before it is put
    # on the AI queue.  Resume only that transient state when Gmail retries;
    # terminal/queued rows remain idempotent.
    if not created and not reprocess and str(row.get("processing_status") or "").upper() != "FILTERED":
        return None
    previous_status=row.get("processing_status")
    if not reprocess and store.is_duplicate_content(
        mailbox["candidate_id"], row["id"], decoded["message_hash"], decoded["body_hash"],
        decoded.get("subject"),
    ):
        store.mark_message_status(row["id"], "DUPLICATE_CONTENT", reason="DUPLICATE_MESSAGE")
        # A dropped mail that was carrying an interview must not vanish without
        # trace. Dedupe runs before the calendar fallback below, so this is the
        # one place that can see an invite being discarded.
        _publish_ignored_interview(mailbox, decoded, safe, "DUPLICATE_CONTENT", "DUPLICATE_MESSAGE")
        return None
    for attachment in processed:
        if attachment.get("checksum"):
            store.save_attachment(row["id"], attachment)
    if str(decoded.get("message_direction") or "").upper() == "OUTBOUND":
        if reprocess:
            store.archive_event_for_message(row["id"], status="IGNORED_NOT_OFFER_RELATED", reason="OUTBOUND_MESSAGE")
        store.mark_message_status(row["id"], "IGNORED_NOT_OFFER_RELATED", reason="OUTBOUND_MESSAGE")
        if reprocess:
            store.mark_reprocessed(row["id"], previous_status, "IGNORED_NOT_OFFER_RELATED", "MESSAGE_DIRECTION_CORRECTION")
        return None
    from services.calendar_invite_parser import trusted_interview_result
    calendar_result = trusted_interview_result(decoded, safe)
    if not route["send_to_ai"] and not calendar_result:
        if reprocess:
            store.archive_event_for_message(row["id"], status="IGNORED_NOT_OFFER_RELATED", reason=route["reason"])
        store.mark_message_status(row["id"], "IGNORED_NOT_OFFER_RELATED", reason=route["reason"])
        _publish_ignored_interview(mailbox, decoded, safe, "IGNORED_NOT_OFFER_RELATED", route["reason"])
        if reprocess: store.mark_reprocessed(row["id"],previous_status,"IGNORED_NOT_OFFER_RELATED","HISTORICAL_RULE_RESCAN")
        return None
    routed_status = str((route.get("context") or {}).get("status") or "")
    if not reprocess and routed_status in OFFER_CASE_STATUSES and store.is_duplicate_offer_attachment(mailbox["candidate_id"], row["id"]):
        store.mark_message_status(row["id"], "DUPLICATE_OFFER_ATTACHMENT", reason="DUPLICATE_OFFER_ATTACHMENT")
        return None
    if calendar_result:
        result, model, duration = calendar_result, "rfc5545-authenticated", 0
    elif defer_ai:
        store.mark_message_status(row["id"], "AI_QUEUED", reason="DURABLE_AI_QUEUE")
        _publish("mail_ai_queued", candidate_id=mailbox.get("candidate_id"), gmail_message_id=decoded.get("provider_message_id"), processing_status="AI Queued")
        return None
    else:
        try:
            _publish("mail_ai_analyzing", candidate_id=mailbox.get("candidate_id"), gmail_message_id=decoded.get("provider_message_id"), processing_status="AI Analyzing")
            result, model, duration = analyze(decoded, safe)
        except Exception as exc:
            # Infrastructure failure is never evidence of a recruitment outcome.
            # Persist only a neutral retry result; do not derive candidate,
            # interview, payment, offer, or lifecycle state from keywords.
            result = _failure_review_result(decoded, exc)
            model = f"unavailable:{getattr(exc, 'code', type(exc).__name__).lower()}"
            duration = 0
            failure_code = getattr(exc, "code", None) or "OLLAMA_INTERNAL_ERROR"
            if (
                failure_code in _DETERMINISTIC_AI_FAILURES
                and int(row.get("ai_retry_count") or 0) >= _VALIDATION_RETRY_ALLOWANCE
            ):
                # The model answered; the answer did not validate. Re-running
                # identical input mostly reproduces the identical refusal, and
                # the queue proved it: two mails reached ten attempts on
                # OLLAMA_SCHEMA_VALIDATION_FAILED and would have been parked as
                # MAX_ATTEMPTS_EXHAUSTED, which names the wrong cause and hides
                # a decision an operator can act on. A few attempts still run,
                # because sampling occasionally produces a valid result; after
                # that the mail is parked where the audit already looks for it.
                logger.warning(
                    "Recruitment email parked after repeated validation failure code=%s attempts=%s",
                    failure_code, row.get("ai_retry_count"),
                )
                store.mark_message_status(
                    row["id"], "VALIDATION_FAILED", reason=failure_code, error_code=failure_code,
                )
                try:
                    store.record_analysis(
                        row["id"], mailbox["candidate_id"], result,
                        model=model, processing_status="VALIDATION_FAILED",
                        error_code=failure_code, error_message=str(exc),
                    )
                except Exception:
                    logger.debug("Unable to persist validation-failure analysis", exc_info=True)
                return None
            logger.warning("Recruitment email queued for semantic retry code=%s", failure_code)
            store.mark_message_status(
                row["id"], "AI_RETRY_PENDING", reason=failure_code, error_code=failure_code,
            )
            if (
                result.get("primary_status") == "MANUAL_REVIEW_REQUIRED"
                and not critical_attachment_failure
            ):
                # Unknown/ambiguous mail is not user-actionable while AI is down.
                # Keep its analysis and retry state for recovery, but do not turn
                # infrastructure failure into a false-positive review record.
                try:
                    store.record_analysis(
                        row["id"], mailbox["candidate_id"], result,
                        model=model, processing_status="RETRY_PENDING",
                        error_code=failure_code, error_message=str(exc),
                    )
                except Exception:
                    logger.debug("Unable to persist hidden retry analysis", exc_info=True)
                return None
    if critical_attachment_failure and (not result.get("is_selection_or_offer_related") or not result.get("should_create_review_record")):
        result = _failure_review_result(decoded, RuntimeError("Critical employment attachment extraction failed"))
        result["reason"] = "A potentially important employment attachment could not be extracted"
        result["risk_flags"] = ["ATTACHMENT_EXTRACTION_FAILED"]
    if job_board_notification(decoded.get("sender_email", "")):
        # Marked not-relevant so it takes the existing ignore path: no event, no
        # lifecycle status, no notification. The analysis is still recorded, so
        # the decision stays auditable.
        result["is_selection_or_offer_related"] = False
        result["should_create_review_record"] = False
        result["ignore_reason"] = "JOB_BOARD_NOTIFICATION"
    if not result.get("is_selection_or_offer_related") or not result.get("should_create_review_record") or result.get("primary_status") not in TRACKED_STATUSES:
        status = "IGNORED_LOW_CONFIDENCE" if result.get("primary_status") == "IGNORED_LOW_CONFIDENCE" else "IGNORED_NOT_OFFER_RELATED"
        if reprocess:
            store.archive_event_for_message(row["id"], status=status, reason=result.get("ignore_reason") or "AI_NOT_OFFER_RELATED", result=result)
        store.mark_message_status(row["id"], status, reason=result.get("ignore_reason") or "AI_NOT_OFFER_RELATED")
        try:
            store.record_analysis(row["id"],mailbox["candidate_id"],result,model=model,processing_status="COMPLETED")
        except Exception:
            logger.debug("Unable to persist non-relevant AI analysis", exc_info=True)
        if reprocess:
            store.mark_reprocessed(row["id"], previous_status, status, "HISTORICAL_SEMANTIC_RESCAN")
        return None
    if not result.get("evidence") and result.get("primary_status") != "MANUAL_REVIEW_REQUIRED":
        result.update(primary_status="MANUAL_REVIEW_REQUIRED", status="MANUAL_REVIEW_REQUIRED",
                      classification="needs_review", candidate_status="Needs Review",
                      requires_manual_review=True, reason="The AI result lacks source-supported evidence")
    if reprocess:
        # Downstream booking must distinguish delayed historical recovery from
        # a live message. This marker is persisted in the structured analysis
        # so retries preserve the same safety decision.
        result["_historical_reprocess"] = True
        historical_classification = store.canonical_classification(result)
        if historical_classification in {
            "interview_confirmed", "interview_rescheduled",
        }:
            from services.interview_auto_booking import (
                should_suppress_historical_notification,
            )
            result["_suppress_monitoring_notification"] = (
                should_suppress_historical_notification(
                    result, historical_classification,
                )
            )
    if store.is_duplicate_thread_status(mailbox["candidate_id"], row["id"], result["primary_status"]):
        store.mark_message_status(row["id"], "DUPLICATE_OFFER_EVENT", reason="DUPLICATE_THREAD_STATUS")
        return None
    event = (store.create_or_reprocess_event(mailbox["candidate_id"],row["id"],result,model=model,duration_ms=duration,reason="HISTORICAL_RULE_RESCAN") if reprocess else store.create_event(mailbox["candidate_id"], row["id"], result, model=model, duration_ms=duration))
    logger.info("Recruitment classification saved: event=%s source=%s", event.get("id"), result.get("classification_source", "OLLAMA"))
    if reprocess: store.mark_reprocessed(row["id"],previous_status,"EVENT_CREATED","HISTORICAL_RULE_RESCAN")
    suppress_notification = bool(result.get("_suppress_monitoring_notification"))
    if not suppress_notification:
        from services.recruitment_notifications import notify_detection
        notify_detection(event)
    notification = event.get("notification") or {}
    common = {
        "notification_id": notification.get("id"), "candidate_id": event.get("candidate_id"),
        "candidate_name": notification.get("candidate_name"), "company_name": notification.get("company_name"),
        "classification": event.get("classification") or result.get("classification"),
        "status": event.get("candidate_status") or result.get("candidate_status"),
        "confidence": round(float(event.get("confidence") or 0) * 100),
        "priority": notification.get("priority"),
    }
    _publish("mail_classified", **common)
    if not suppress_notification:
        _publish("mail_needs_review" if common["classification"] == "needs_review" else "important_mail_detected", **common)
    if event.get("candidate_status_updated"):
        _publish("candidate_status_updated", **common)
    if notification:
        _publish("notification_created", **common)
    if common["classification"] in {"interview_confirmed", "interview_rescheduled", "interview_cancelled"}:
        interview = result.get("interview") or {}
        _publish(
            "interview_detected", **common,
            interview_round=interview.get("round"), interview_date=interview.get("date"),
            interview_time=interview.get("time"), timezone=interview.get("timezone"),
        )
        _publish("auto_booking_started", **common, processing_status="Validating Booking")
        try:
            from services.interview_auto_booking import execute_auto_booking
            outcome = execute_auto_booking(mailbox=mailbox, message=decoded, event=event, result=result)
            booking = outcome.get("booking") or {}
            audit = outcome.get("audit") or {}
            # The schedule comes from the booking that was actually written,
            # not from the model's reading of the email: the two can differ
            # (a timezone is normalised, a reschedule moves an existing slot),
            # and a notification that announces a time nobody is booked for is
            # worse than no notification.
            booked_round = booking.get("interview_round") or interview.get("round")
            booking_event = {
                **common, "status": outcome.get("status"), "booking_id": booking.get("id"),
                "booking_audit_id": audit.get("id"),
                "candidate_name": booking.get("name") or common.get("candidate_name"),
                "company_name": booking.get("interview_company") or common.get("company_name"),
                "interview_round": booked_round,
                "interview_date": booking.get("date") or interview.get("date"),
                "interview_time": booking.get("time") or interview.get("time"),
                "start_time": booking.get("time") or interview.get("time"),
                "end_time": booking.get("time_end") or interview.get("end_time"),
                "timezone": (
                    "Asia/Kolkata" if booking.get("date") else interview.get("timezone")
                ),
                "booking_url": (
                    f"/daily-ops?bookingId={booking.get('id')}" if booking.get("id") else ""
                ),
                "failure_code": outcome.get("failure_code"),
                # Present only when the booking was refused, and already phrased
                # for a person by the validator.
                "block_reason": (outcome.get("block_reason") or {}).get("reason"),
                "block_reason_code": (outcome.get("block_reason") or {}).get("reason_code"),
            }
            _publish(outcome["event_type"], **booking_event)
            event["auto_booking"] = outcome
            if outcome.get("notification"):
                event["notification"] = outcome["notification"]
                _publish("notification_created", **common, booking_id=booking.get("id"), booking_status=outcome.get("status"))
        except Exception as exc:
            logger.exception("Automatic interview booking failed event=%s code=%s", event.get("id"), type(exc).__name__)
            _publish("mail_processing_failed", **common, processing_status="Processing Failed", error_code=type(exc).__name__)
    return event
