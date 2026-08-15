"""Single source of truth for the Selection and Offer Review visibility rule."""

from __future__ import annotations

from typing import Any


ALLOWED_STATUSES = (
    "SELECTED", "FINAL_SELECTION_CONFIRMED",
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED",
    "OFFER_ACCEPTED", "JOINING_CONFIRMED",
    "JOINED", "POST_SELECTION_ONBOARDING",
    "INTERVIEW_CONFIRMED", "INTERVIEW_SHORTLISTED", "INTERVIEW_PROPOSED", "OFFER_NEEDS_REVIEW", "JOINING_NEEDS_REVIEW", "SELECTION_NEEDS_REVIEW",
    "MANUAL_REVIEW_REQUIRED",
)
IGNORED_STATUSES = {
    "IGNORED_NOT_OFFER_RELATED", "IGNORED_LOW_CONFIDENCE", "NO_RELEVANT_STATUS",
}
IGNORED_REVIEW_STATUSES = {"IGNORED", "FALSE_POSITIVE", "DUPLICATE"}
STRONG_SIGNAL_PHRASES = (
    "you have been selected", "selected for the role", "selected for the position",
    "selection confirmed", "final selection", "we are pleased to offer",
    "we are delighted to offer", "offer letter attached", "employment offer",
    "appointment letter", "letter of appointment", "offer approved", "offer released",
    "offer is being processed", "joining date", "date of joining", "welcome aboard",
    "employee onboarding", "pre-joining formalities", "report for joining",
)


def _evidence(event: dict[str, Any]) -> list[Any]:
    structured = event.get("structured_result") or {}
    return structured.get("evidence") or [] if isinstance(structured, dict) else []


def has_strong_selection_or_offer_signal(event: dict[str, Any]) -> bool:
    structured = event.get("structured_result") or {}
    if isinstance(structured, dict) and structured.get("is_selection_or_offer_related") is True:
        meanings = " ".join(str(item.get("meaning") or "") for item in _evidence(event) if isinstance(item, dict)).upper()
        if any(status in meanings for status in ALLOWED_STATUSES if status != "MANUAL_REVIEW_REQUIRED"):
            return True
    text = " ".join(str(event.get(key) or "") for key in ("subject", "summary", "sender_name", "sender_email"))
    text += " " + str(structured)
    lowered = text.casefold()
    return any(phrase in lowered for phrase in STRONG_SIGNAL_PHRASES)


def should_show_in_selection_offer_review(event: dict[str, Any]) -> bool:
    status = str(event.get("primary_status") or event.get("status") or "").upper()
    review_status = str(event.get("review_status") or "").upper()

    # Only show specific high-value statuses
    if status not in ALLOWED_STATUSES or status in IGNORED_STATUSES:
        return False
    if review_status in IGNORED_REVIEW_STATUSES or event.get("visible_in_offer_review") is False:
        return False

    # Require evidence and high confidence
    evidence = _evidence(event)
    validation = str(event.get("validation_status") or (event.get("structured_result") or {}).get("validation_status") or "").upper()
    manual_audit_keep = (
        status == "MANUAL_REVIEW_REQUIRED"
        and str(event.get("cleanup_version") or "") == "manual_content_audit_keep_v1"
    )
    # AI availability is an infrastructure state, not relevance evidence.
    # Timeout-only MANUAL_REVIEW_REQUIRED rows must remain hidden while the
    # recovery worker retries them. A manual audit or strong source evidence
    # can still make a genuinely important message visible.
    manual_review_visible = manual_audit_keep
    if status == "MANUAL_REVIEW_REQUIRED" and not manual_review_visible:
        meanings = {
            str(item.get("meaning") or "").upper()
            for item in evidence if isinstance(item, dict)
        }
        strong_meanings=(set(ALLOWED_STATUSES)-{"MANUAL_REVIEW_REQUIRED"}) | {
            "JOB_SELECTION_CONFIRMED","OFFER_RECEIVED","OFFER_ACCEPTED",
            "JOINING_CONFIRMED","ONBOARDING_STARTED",
        }
        if not meanings.intersection(strong_meanings):
            return False
    if not manual_review_visible and (not evidence or float(event.get("confidence") or 0) < 0.8):
        return False

    # A confirmed interview asserts a scheduled meeting, so it must carry a
    # date or it is a generic "we'll contact you" note. A shortlist asserts no
    # such thing: being shortlisted is itself the outcome and the interview is
    # not scheduled yet, so requiring a date here hid every genuine shortlist.
    if status == "INTERVIEW_CONFIRMED":
        structured = event.get("structured_result") or {}
        interview = structured.get("interview") or {} if isinstance(structured, dict) else {}
        has_date = bool(str(interview.get("date") or "").strip())
        if not has_date:
            return False  # Don't show generic "we'll contact you" messages

    return True


def cleanup_reason(event: dict[str, Any]) -> str | None:
    """Return an audit reason for a historical row that must be archived."""
    if should_show_in_selection_offer_review(event):
        return None

    status = str(event.get("primary_status") or "").upper()
    subject = str(event.get("subject") or "").casefold()
    sender = " ".join(str(event.get(key) or "") for key in ("sender_name", "sender_email")).casefold()
    summary = str(event.get("summary") or "").casefold()
    if "interview" in subject:
        return "INTERVIEW_OR_ASSESSMENT"

    # Filter out noise - profile views, recommendations, generic updates
    noise_patterns = [
        ("profile viewed", "PROFILE_VIEW_NOTIFICATION"),
        ("resume viewed", "PROFILE_VIEW_NOTIFICATION"),
        ("recruiter", "GENERIC_RECRUITER_MESSAGE"),  # "recruiters are noticing"
        ("job recommendation", "JOB_RECOMMENDATION"),
        ("recommended jobs", "JOB_RECOMMENDATION"),
        ("jobs for you", "JOB_RECOMMENDATION"),
        ("job alert", "JOB_RECOMMENDATION"),
        ("similar jobs", "JOB_RECOMMENDATION"),
        ("featured jobs", "JOB_RECOMMENDATION"),
        ("credit score", "NON_RECRUITMENT_NOTIFICATION"),
        ("consumer credit", "NON_RECRUITMENT_NOTIFICATION"),
        ("application status", "APPLICATION_UPDATE"),
        ("status of your", "APPLICATION_UPDATE"),
        ("appeared in", "SEARCH_APPEARANCE_NOTIFICATION"),
        ("searches", "SEARCH_APPEARANCE_NOTIFICATION"),
    ]

    for pattern, reason in noise_patterns:
        if pattern in subject or pattern in summary:
            return reason

    # Filter out interview notifications without actual slots
    if status in ("INTERVIEW_CONFIRMED", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE"):
        structured = event.get("structured_result") or {}
        interview = structured.get("interview") or {} if isinstance(structured, dict) else {}
        has_date = bool(str(interview.get("date") or "").strip())
        if not has_date:
            return "INTERVIEW_WITHOUT_SLOT"

    # Original filters
    if any(term in subject for term in ("assessment", "coding test")):
        return "INTERVIEW_OR_ASSESSMENT"
    if any(term in subject for term in ("rejection", "regret to inform")):
        return "NON_OFFER_RECRUITMENT_MAIL"
    if any(portal in subject + " " + sender for portal in ("foundit", "monster", "naukri", "linkedin jobs", "indeed", "shine", "timesjobs")):
        return "JOB_PORTAL_PROMOTION"
    if float(event.get("confidence") or 0) < 0.8:
        return "LOW_CONFIDENCE"
    if not _evidence(event):
        return "NO_EVIDENCE"

    # Not in allowed statuses
    if status not in ALLOWED_STATUSES:
        return "NOT_ACTIONABLE_STATUS"

    return "NO_QUALIFIED_SELECTION_OR_OFFER_EVIDENCE"


# Requested public spelling; Python callers should use the snake-case function above.
shouldShowInSelectionOfferReview = should_show_in_selection_offer_review


def qualified_event_sql(alias: str = "e") -> tuple[str, list[Any]]:
    """SQL counterpart used by review, timeline, metrics and dashboard queries."""
    predicate = f"""{alias}.primary_status=ANY(%s)
      AND {alias}.primary_status NOT IN('IGNORED_NOT_OFFER_RELATED','IGNORED_LOW_CONFIDENCE','NO_RELEVANT_STATUS')
      AND {alias}.review_status NOT IN('IGNORED','FALSE_POSITIVE','DUPLICATE')
      AND COALESCE({alias}.visible_in_offer_review,true)=true
      AND (({alias}.primary_status='MANUAL_REVIEW_REQUIRED'
        AND {alias}.cleanup_version='manual_content_audit_keep_v1') OR (
        {alias}.confidence>=0.8
        AND jsonb_array_length(COALESCE({alias}.structured_result->'evidence','[]'::jsonb))>0
      ))
      AND ({alias}.primary_status NOT IN('INTERVIEW_CONFIRMED','INTERVIEW_SHORTLISTED') OR (
        COALESCE(({alias}.structured_result->'interview'->>'date'),'') <> ''
      ))"""
    return predicate, [list(ALLOWED_STATUSES)]
