"""Booking-side automation contract for recruitment mail.

Claude decides *what a mail means*.  This module deliberately does not score
mail, infer relevance, or inspect model prompts.  It consumes that final
decision and gives the mailbox worker one durable, non-human state machine.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class AutomationState(StrEnum):
    AUTO_BOOK = "AUTO_BOOK"
    AUTO_IGNORE = "AUTO_IGNORE"
    AI_RETRY_PENDING = "AI_RETRY_PENDING"
    AUTO_BOOKED = "AUTO_BOOKED"
    AUTO_CANCELLED = "AUTO_CANCELLED"
    AUTO_RESCHEDULED = "AUTO_RESCHEDULED"


DECISION_STATES = frozenset({
    AutomationState.AUTO_BOOK,
    AutomationState.AUTO_IGNORE,
    AutomationState.AI_RETRY_PENDING,
})
OUTCOME_STATES = frozenset({
    AutomationState.AUTO_BOOKED,
    AutomationState.AUTO_CANCELLED,
    AutomationState.AUTO_RESCHEDULED,
})
ALL_STATES = DECISION_STATES | OUTCOME_STATES
ACTIONABLE_CLASSIFICATIONS = frozenset({
    "interview_confirmed", "interview_rescheduled", "interview_cancelled",
})
LEGACY_UNCERTAIN_STATUSES = frozenset({
    "MANUAL_REVIEW_REQUIRED", "NEEDS_REVIEW", "REVIEW_REQUIRED",
    "VALIDATION_FAILED", "AI_FAILED_TERMINAL",
})


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def classification_of(result: Mapping[str, Any]) -> str:
    return str(
        result.get("classification") or result.get("primary_status") or result.get("status") or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")


def decision_for(result: Mapping[str, Any]) -> AutomationState:
    """Return the only permitted automated decision for a stored result.

    A supplied decision is Claude's authority.  Compatibility handling never
    upgrades uncertain legacy rows into a booking: they become retryable until
    Claude emits a final decision.  Established, actionable legacy results are
    accepted only when they contain source evidence; booking performs the
    stricter deterministic source/payment/candidate/lifecycle checks later.
    """
    explicit = _text(result.get("automation_decision") or result.get("booking_decision"))
    if explicit in {state.value for state in DECISION_STATES}:
        return AutomationState(explicit)

    status = _text(result.get("primary_status") or result.get("status"))
    validation = _text(result.get("validation_status"))
    classification = classification_of(result)
    if (
        bool(result.get("requires_manual_review"))
        or status in LEGACY_UNCERTAIN_STATUSES
        or validation in LEGACY_UNCERTAIN_STATUSES
        or classification == "needs_review"
    ):
        return AutomationState.AI_RETRY_PENDING
    if classification in ACTIONABLE_CLASSIFICATIONS:
        return (
            AutomationState.AUTO_BOOK
            if bool(result.get("evidence"))
            else AutomationState.AI_RETRY_PENDING
        )
    return AutomationState.AUTO_IGNORE


def apply_decision(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a projection with no human-review action required."""
    value = dict(result)
    decision = decision_for(value)
    value["automation_decision"] = decision.value
    value["requires_manual_review"] = False
    value["automation_managed"] = True
    if decision is AutomationState.AI_RETRY_PENDING:
        value["automation_state"] = decision.value
        value["validation_status"] = "RETRY_PENDING"
        value["ai_status"] = decision.value
    return value


def outcome_for(booking_outcome: Mapping[str, Any]) -> AutomationState:
    """Translate an executor result without ever claiming an unpersisted slot."""
    status = _text(booking_outcome.get("status"))
    if status in {"AUTO BOOKED", "APPROVED & BOOKED", "ALREADY PROCESSED"}:
        return AutomationState.AUTO_BOOKED
    if status == "RESCHEDULED":
        return AutomationState.AUTO_RESCHEDULED
    if status == "CANCELLED":
        return AutomationState.AUTO_CANCELLED
    # A stale lifecycle replay or exact duplicate is terminal and has no work
    # left to do.  All other deterministic/infrastructure failures stay in the
    # automatic queue; a payment or candidate record can become valid later.
    if _text(booking_outcome.get("failure_code")) in {
        "STALE_INTERVIEW_EVENT", "DUPLICATE_BOOKING", "NOT_ACTIONABLE", "PAST_INTERVIEW",
    }:
        return AutomationState.AUTO_IGNORE
    return AutomationState.AI_RETRY_PENDING
