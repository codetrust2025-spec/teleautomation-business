"""Data room — business opportunities & partners (not job-seeking candidates).

Stores inbound contacts who offer services, partnerships, candidate supply,
or other alignment opportunities for future follow-up.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

from core.config import DATA_DIR

_FILE = os.path.join(DATA_DIR, "data_room", "opportunities.json")
_lock = Lock()

VALID_STATUSES = frozenset({
    "new",
    "reviewing",
    "aligned",
    "on_hold",
    "closed",
    "not_relevant",
})

VALID_TYPES = frozenset({
    "support_provider",
    "vendor_candidates",
    "partnership",
    "recruitment",
    "referral",
    "other",
})

# Legacy: credentials used to be mixed into opportunities; kept for purge only.
CREDENTIALS_TAG = "dashboard_credentials"
_LEGACY_CREDENTIAL_TYPES = frozenset({"dashboard_login"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _empty() -> dict:
    return {"opportunities": [], "updated_at": None}


def _load() -> dict:
    with _lock:
        if not os.path.exists(_FILE):
            return _empty()
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _empty()
            data.setdefault("opportunities", [])
            data.setdefault("updated_at", None)
            return data
        except (OSError, json.JSONDecodeError):
            return _empty()


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    data = dict(data)
    data["updated_at"] = _now_iso()
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


def _normalize_type(value: str | None) -> str:
    t = (value or "other").strip().lower().replace(" ", "_")
    return t if t in VALID_TYPES else "other"


def _normalize_status(value: str | None) -> str:
    s = (value or "new").strip().lower().replace(" ", "_")
    return s if s in VALID_STATUSES else "new"


def _clean(value, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def crm_link_key(slot: str, user_id: int) -> str:
    return f"{slot}:{int(user_id)}"


def _contact_complete(row: dict) -> bool:
    return bool(
        (row.get("phone") or "").strip()
        or (row.get("whatsapp") or "").strip()
        or (row.get("email") or "").strip()
    )


def _normalize_row(raw: dict) -> dict:
    now = _now_iso()
    row = {
        "id": _clean(raw.get("id")) or _new_id(),
        "opportunity_type": _normalize_type(raw.get("opportunity_type")),
        "status": _normalize_status(raw.get("status")),
        "name": _clean(raw.get("name")),
        "phone": _clean(raw.get("phone")),
        "whatsapp": _clean(raw.get("whatsapp")),
        "email": _clean(raw.get("email")).lower(),
        "preferred_contact": _clean(raw.get("preferred_contact"), default="whatsapp"),
        "username": _clean(raw.get("username")).lstrip("@"),
        "telegram_user_id": int(raw.get("telegram_user_id") or 0),
        "account_id": _clean(raw.get("account_id")),
        "tech_stack": _clean(raw.get("tech_stack")),
        "volume_hint": _clean(raw.get("volume_hint")),
        "summary": _clean(raw.get("summary")),
        "source_snippet": _clean(raw.get("source_snippet"))[:2000],
        "notes": _clean(raw.get("notes")),
        "tags": [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()],
        "senior_route": _clean(raw.get("senior_route")),
        "inbox_ref": _clean(raw.get("inbox_ref")),
        "linked_crm_key": _clean(raw.get("linked_crm_key")),
        "contact_complete": bool(raw.get("contact_complete")),
        "created_at": _clean(raw.get("created_at")) or now,
        "updated_at": _clean(raw.get("updated_at")) or now,
        "last_contact_at": _clean(raw.get("last_contact_at")) or now,
    }
    if not row.get("inbox_ref") and row.get("linked_crm_key"):
        row["inbox_ref"] = row["linked_crm_key"]
    row["contact_complete"] = _contact_complete(row)
    return row


def _is_partner_opportunity(row: dict) -> bool:
    if CREDENTIALS_TAG in (row.get("tags") or []):
        return False
    if (row.get("opportunity_type") or "") in _LEGACY_CREDENTIAL_TYPES:
        return False
    return True


def purge_legacy_credential_opportunities() -> int:
    """Remove dashboard login rows mistakenly stored as opportunities."""
    data = _load()
    before = len(data.get("opportunities") or [])
    data["opportunities"] = [
        r for r in (data.get("opportunities") or [])
        if _is_partner_opportunity(r)
    ]
    removed = before - len(data["opportunities"])
    if removed:
        _save(data)
    return removed


def list_opportunities(
    *,
    status: str | None = None,
    opportunity_type: str | None = None,
    query: str | None = None,
) -> list[dict]:
    rows = [
        _normalize_row(r) for r in _load().get("opportunities") or []
        if _is_partner_opportunity(r)
    ]
    if status:
        st = _normalize_status(status)
        rows = [r for r in rows if r["status"] == st]
    if opportunity_type:
        ot = _normalize_type(opportunity_type)
        rows = [r for r in rows if r["opportunity_type"] == ot]
    if query:
        q = query.strip().lower()
        if q:
            def _hit(r: dict) -> bool:
                blob = " ".join(
                    (
                        r.get("name") or "",
                        r.get("username") or "",
                        r.get("tech_stack") or "",
                        r.get("summary") or "",
                        r.get("notes") or "",
                        r.get("account_id") or "",
                        r.get("phone") or "",
                        r.get("whatsapp") or "",
                        r.get("email") or "",
                    )
                ).lower()
                return q in blob
            rows = [r for r in rows if _hit(r)]
    rows.sort(key=lambda r: r.get("last_contact_at") or "", reverse=True)
    return rows


def get_opportunity(oid: str) -> dict | None:
    oid = (oid or "").strip()
    for row in _load().get("opportunities") or []:
        if (row.get("id") or "") == oid:
            return _normalize_row(row)
    return None


def create_opportunity(payload: dict) -> dict:
    data = _load()
    row = _normalize_row(payload)
    data["opportunities"].append(row)
    _save(data)
    return row


def update_opportunity(oid: str, patch: dict) -> dict | None:
    data = _load()
    oid = (oid or "").strip()
    for i, row in enumerate(data.get("opportunities") or []):
        if (row.get("id") or "") != oid:
            continue
        merged = dict(row)
        allowed = {
            "opportunity_type", "status", "name", "phone", "whatsapp", "email",
            "preferred_contact", "username", "telegram_user_id", "account_id",
            "tech_stack", "volume_hint", "summary", "source_snippet", "notes",
            "tags", "senior_route", "inbox_ref", "linked_crm_key", "last_contact_at",
        }
        for key, val in (patch or {}).items():
            if key not in allowed:
                continue
            if key == "opportunity_type":
                merged["opportunity_type"] = _normalize_type(val)
            elif key == "status":
                merged["status"] = _normalize_status(val)
            elif key == "tags" and isinstance(val, list):
                merged["tags"] = [str(t).strip() for t in val if str(t).strip()]
            elif key == "telegram_user_id":
                merged["telegram_user_id"] = int(val or 0)
            else:
                merged[key] = _clean(val) if isinstance(val, str) else val
        merged["updated_at"] = _now_iso()
        normalized = _normalize_row(merged)
        data["opportunities"][i] = normalized
        _save(data)
        return normalized
    return None


def delete_opportunity(oid: str) -> bool:
    data = _load()
    oid = (oid or "").strip()
    before = len(data.get("opportunities") or [])
    data["opportunities"] = [
        r for r in (data.get("opportunities") or []) if (r.get("id") or "") != oid
    ]
    if len(data["opportunities"]) == before:
        return False
    _save(data)
    return True


def find_by_crm_link(slot: str, user_id: int) -> dict | None:
    key = crm_link_key(slot, user_id)
    for row in _load().get("opportunities") or []:
        if (row.get("linked_crm_key") or "") == key:
            return _normalize_row(row)
    return None


def upsert_from_telegram(
    slot: str,
    user_id: int,
    *,
    opportunity_type: str,
    name: str = "",
    username: str = "",
    tech_stack: str = "",
    volume_hint: str = "",
    summary: str = "",
    source_snippet: str = "",
    senior_route: str = "",
    phone: str = "",
    whatsapp: str = "",
    email: str = "",
    notes_append: str = "",
) -> dict:
    """Create or refresh a data-room row from an inbox thread."""
    key = crm_link_key(slot, user_id)
    existing = find_by_crm_link(slot, user_id)
    now = _now_iso()
    if existing:
        notes = existing.get("notes") or ""
        if notes_append and notes_append not in notes:
            notes = f"{notes}\n{notes_append}".strip() if notes else notes_append
        patch = {
            "opportunity_type": opportunity_type or existing.get("opportunity_type"),
            "name": name or existing.get("name"),
            "username": username or existing.get("username"),
            "tech_stack": tech_stack or existing.get("tech_stack"),
            "volume_hint": volume_hint or existing.get("volume_hint"),
            "summary": summary or existing.get("summary"),
            "source_snippet": source_snippet or existing.get("source_snippet"),
            "phone": phone or existing.get("phone"),
            "whatsapp": whatsapp or existing.get("whatsapp"),
            "email": email or existing.get("email"),
            "notes": notes,
            "last_contact_at": now,
        }
        updated = update_opportunity(existing["id"], patch)
        return updated or existing

    return create_opportunity({
        "opportunity_type": opportunity_type,
        "status": "new",
        "name": name,
        "username": username,
        "telegram_user_id": int(user_id),
        "account_id": slot,
        "tech_stack": tech_stack,
        "volume_hint": volume_hint,
        "summary": summary,
        "source_snippet": source_snippet,
        "phone": phone,
        "whatsapp": whatsapp,
        "email": email,
        "inbox_ref": key,
        "notes": notes_append,
        "linked_crm_key": key,
        "last_contact_at": now,
    })


def stats_summary() -> dict:
    rows = list_opportunities()
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_type[r["opportunity_type"]] = by_type.get(r["opportunity_type"], 0) + 1
    needs_contact = sum(1 for r in rows if not r.get("contact_complete"))
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_type": by_type,
        "needs_contact": needs_contact,
    }
