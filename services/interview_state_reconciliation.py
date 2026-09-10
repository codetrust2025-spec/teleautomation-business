"""Read-only consistency report across interview booking surfaces."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slot_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (_text(row.get("date"))[:10], _text(row.get("time"))[:5], _text(row.get("time_end"))[:5])


def _finding(code: str, severity: str, *, candidate_id: str = "", booking_id: str = "", detail: str = "", sources: Iterable[str] = ()) -> dict[str, Any]:
    return {"code": code, "severity": severity, "candidate_id": candidate_id, "booking_id": booking_id, "detail": detail, "sources": list(sources)}


def build_reconciliation_report(
    *, candidates: Iterable[Mapping[str, Any]], analyses: Iterable[Mapping[str, Any]],
    audits: Iterable[Mapping[str, Any]], notifications: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare supplied snapshots without mutating any of them.

    The caller owns data retrieval.  This separation keeps the engine safe for
    production inspection and straightforward to run against exported data.
    """
    candidate_rows = [dict(row) for row in candidates]
    audit_rows = [dict(row) for row in audits]
    notification_rows = [dict(row) for row in notifications]
    analysis_rows = [dict(row) for row in analyses]
    confirmed = { _text(row.get("id")): row for row in candidate_rows if bool(row.get("slot_confirmed")) }
    findings: list[dict[str, Any]] = []

    audits_by_booking: dict[str, list[dict[str, Any]]] = {}
    for audit in audit_rows:
        booking_id = _text(audit.get("booking_id"))
        if booking_id:
            audits_by_booking.setdefault(booking_id, []).append(audit)
        if bool(audit.get("auto_booked")) and booking_id not in confirmed:
            findings.append(_finding(
                "AUTO_BOOKED_WITHOUT_PERSISTED_SLOT", "critical", candidate_id=_text(audit.get("candidate_id")),
                booking_id=booking_id, detail="Booking audit claims automatic success but no confirmed candidate slot exists.",
                sources=("booking_audit", "candidate_store"),
            ))

    for booking_id, slot in confirmed.items():
        if _text(slot.get("interview_booking_source")).lower() == "ai_auto_booked" and not audits_by_booking.get(booking_id):
            findings.append(_finding(
                "PERSISTED_AI_SLOT_WITHOUT_AUDIT", "high", candidate_id=booking_id, booking_id=booking_id,
                detail="Confirmed AI-created slot has no booking audit row.", sources=("candidate_store", "booking_audit"),
            ))

    for notification in notification_rows:
        status = _text(notification.get("booking_status")).casefold()
        booking_id = _text(notification.get("booking_id"))
        if status in {"auto booked", "approved & booked", "rescheduled"} and booking_id not in confirmed:
            findings.append(_finding(
                "NOTIFICATION_CLAIMS_BOOKED_WITHOUT_SLOT", "critical", candidate_id=_text(notification.get("candidate_id")),
                booking_id=booking_id, detail="Notification claims a persisted booking that candidate storage does not contain.",
                sources=("notification", "candidate_store"),
            ))
        if _text(notification.get("booking_audit_id")) and not any(_text(a.get("id")) == _text(notification.get("booking_audit_id")) for a in audit_rows):
            findings.append(_finding(
                "NOTIFICATION_AUDIT_MISSING", "high", candidate_id=_text(notification.get("candidate_id")), booking_id=booking_id,
                detail="Notification points to a booking audit that is absent from the snapshot.", sources=("notification", "booking_audit"),
            ))

    analysis_ids = {_text(row.get("id")) for row in analysis_rows}
    for audit in audit_rows:
        analysis_id = _text(audit.get("email_analysis_id"))
        if analysis_id and analysis_id not in analysis_ids:
            findings.append(_finding(
                "BOOKING_AUDIT_ANALYSIS_MISSING", "medium", candidate_id=_text(audit.get("candidate_id")),
                booking_id=_text(audit.get("booking_id")), detail="Booking audit references an absent interview analysis.",
                sources=("booking_audit", "interview_analysis"),
            ))

    overlapping = Counter(_slot_key(row) for row in confirmed.values() if all(_slot_key(row)))
    for key, count in overlapping.items():
        if count > 1:
            findings.append(_finding(
                "DUPLICATE_CONFIRMED_SLOT_TIME", "medium", detail=f"{count} confirmed rows share {key[0]} {key[1]}-{key[2]}.", sources=("candidate_store",)))

    severity_counts = Counter(item["severity"] for item in findings)
    return {
        "mode": "report_only",
        "summary": {"candidates": len(candidate_rows), "analyses": len(analysis_rows), "audits": len(audit_rows), "notifications": len(notification_rows), "findings": len(findings), **dict(severity_counts)},
        "findings": findings,
    }


def load_current_report(*, candidate_id: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Read the four production surfaces and return a report-only snapshot.

    This function deliberately imports its stores late: callers can use the
    pure builder for exports/tests, while this production helper performs no
    update, lock, retry, acknowledgement, or booking action.
    """
    from core.db.connection import get_connection
    from core import recruitment_mail_store as mail_store
    from features import candidate_store

    # ``list_candidates`` collapses clone rows, precisely the rows that hold
    # additional interview slots.  Read the underlying snapshot instead.
    candidates = list(candidate_store._load(force=True).get("candidates") or [])
    if candidate_id:
        candidates = [row for row in candidates if _text(row.get("id")) == candidate_id]
    audits = mail_store.list_booking_audit(candidate_id=candidate_id, limit=limit)
    notifications, _ = mail_store.list_notifications(
        filters={"candidate_id": candidate_id} if candidate_id else {}, limit=min(limit, 100), offset=0,
    )
    params: list[Any] = []
    where = ""
    if candidate_id:
        where = "WHERE candidate_id=%s"
        params.append(candidate_id)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM interview_mail_analyses {where} ORDER BY updated_at DESC LIMIT %s",
            params + [max(1, min(limit, 1000))],
        )
        names = [column.name for column in cur.description]
        analyses = [dict(zip(names, row)) for row in cur.fetchall()]
    return build_reconciliation_report(
        candidates=candidates, analyses=analyses, audits=audits, notifications=notifications,
    )
