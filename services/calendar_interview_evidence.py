"""Deterministic resolution for contradictory calendar-invite relevance.

The model decides ordinary mail relevance.  This module only resolves the
small, auditable case where that decision contradicts an already authenticated
RFC5545 invitation addressed to the monitored candidate.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_MARKETING = re.compile(
    r"\b(webinar|workshop|masterclass|bootcamp|open\s+day|career\s+fair|"
    r"newsletter|training\s+(?:session|program|course)?|public\s+event)\b",
    re.I,
)
_HIRING = re.compile(
    r"\b(interview|discussion|round|screening|assessment|recruit(?:er|ment)?|"
    r"hiring|candidate|shortlist(?:ed)?|selection)\b",
    re.I,
)
_ROLE = re.compile(
    r"\b(developer|engineer|devops|consultant|analyst|architect|tester|qa|"
    r"administrator|specialist|sre|java|python|servicenow|react)\b",
    re.I,
)


def _text(calendar_result: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    interview = calendar_result.get("interview") or {}
    job = calendar_result.get("job") or {}
    calendar = calendar_result.get("calendar") or {}
    return "\n".join(
        str(value or "")
        for value in (
            message.get("subject"), message.get("body"), message.get("html_body"),
            job.get("title"), interview.get("round"), calendar.get("summary"),
            calendar_result.get("summary"), calendar_result.get("evidence_summary"),
        )
    )


def evidence_for(
    calendar_result: Mapping[str, Any] | None,
    message: Mapping[str, Any] | None,
) -> dict[str, bool]:
    """Return explainable evidence flags; never infer a schedule here."""
    result = calendar_result or {}
    mail = message or {}
    interview = result.get("interview") or {}
    calendar = result.get("calendar") or {}
    text = _text(result, mail)
    trusted_request = (
        str(result.get("calendar_validation_status") or "").upper() == "TRUSTED"
        and str(calendar.get("method") or "").upper() in {"REQUEST", "CANCEL"}
        and bool(calendar.get("uid"))
        and bool(calendar.get("has_dtend"))
        and bool(interview.get("date") or str(result.get("classification") or "") == "interview_cancelled")
        and bool(interview.get("meeting_link"))
    )
    return {
        "trusted_request": trusted_request,
        "hiring_context": bool(_HIRING.search(text)),
        "role_context": bool(_ROLE.search(text)),
        "clear_marketing": bool(_MARKETING.search(text)),
    }


def contradiction_resolution(
    relevance: Mapping[str, Any],
    calendar_result: Mapping[str, Any] | None,
    message: Mapping[str, Any] | None,
) -> str:
    """Return ``BOOK``, ``IGNORE`` or ``RETRY`` for a contradictory result.

    Clear marketing always wins.  Otherwise, an authenticated calendar request
    with a meeting link and explicit hiring/role context is stronger evidence
    than a self-contradictory model label.  Anything short of that proof stays
    retryable rather than disappearing.
    """
    evidence = evidence_for(calendar_result, message)
    decision = str(relevance.get("decision") or "").upper()
    kind = str(relevance.get("message_kind") or "").upper()
    contradictory = (
        (decision == "NOT_ESTABLISHED" and kind == "RECIPIENT_HIRING_PROCESS")
        or (decision == "ESTABLISHED" and kind != "RECIPIENT_HIRING_PROCESS")
    )
    if not contradictory:
        return "IGNORE"
    if evidence["clear_marketing"] and kind in {
        "MARKETING_OR_TRAINING", "PUBLIC_EVENT", "NEWSLETTER", "JOB_ADVERTISEMENT",
    }:
        return "IGNORE"
    if evidence["trusted_request"] and (evidence["hiring_context"] or evidence["role_context"]):
        return "BOOK"
    return "RETRY"
