"""Temporary verified payment proofs for public slot confirmation."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

from core.config import DATA_DIR

PENDING_PAYMENT_DIR = os.path.join(DATA_DIR, "pending_slot_payments")
PENDING_PAYMENT_INDEX = os.path.join(PENDING_PAYMENT_DIR, "index.json")
PENDING_PAYMENT_MAX_AGE_HOURS = 6
_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    try:
        with open(PENDING_PAYMENT_INDEX, "r", encoding="utf-8") as handle:
            value = json.load(handle)
            if isinstance(value, dict):
                value.setdefault("proofs", {})
                return value
    except (OSError, ValueError):
        pass
    return {"proofs": {}}


def _save(data: dict) -> None:
    os.makedirs(PENDING_PAYMENT_DIR, exist_ok=True)
    temporary = PENDING_PAYMENT_INDEX + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, PENDING_PAYMENT_INDEX)


def _name_key(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def _extension(mime_type: str, original_name: str) -> str:
    allowed = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/heic": "heic", "image/heif": "heif"}
    normalized = str(mime_type or "").lower().split(";")[0].strip()
    if normalized in allowed:
        return allowed[normalized]
    suffix = str(original_name or "").rsplit(".", 1)[-1].lower()
    if suffix in {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif"}:
        return "jpg" if suffix == "jpeg" else suffix
    raise ValueError("Only payment screenshot image files are allowed")


def save_verified_proof(*, name: str, service_type: str, data: bytes, original_name: str, mime_type: str, amount_due: int, verification: dict, phone: str = "", candidate_id: str = "", technology: str = "", interview_round: str = "", note: str = "") -> dict:
    if not data:
        raise ValueError("Empty payment screenshot")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("File too large (max 8 MB)")
    if not verification.get("booking_eligible"):
        raise ValueError("Payment proof is not verified for booking")
    from features.payment_fraud_detection import assess_payment_proof

    fraud_check = assess_payment_proof(
        data,
        verification,
        candidate_id=candidate_id,
        candidate_name=name,
        candidate_phone=phone,
    )
    if fraud_check["decision"] == "rejected":
        raise ValueError(" ".join(fraud_check["reasons"]))
    proof_id = uuid.uuid4().hex
    extension = _extension(mime_type, original_name)
    filename = f"{proof_id}.{extension}"
    os.makedirs(PENDING_PAYMENT_DIR, exist_ok=True)
    path = os.path.join(PENDING_PAYMENT_DIR, filename)
    temporary = path + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(data)
    os.replace(temporary, path)
    entry = {"id": proof_id, "filename": filename, "original_name": str(original_name or filename)[:160], "mime_type": mime_type or f"image/{extension}", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "name_key": _name_key(name), "service_type": str(service_type or "").strip() or "profile_service", "phone": str(phone or "").strip(), "candidate_id": str(candidate_id or "").strip(), "technology": str(technology or "").strip(), "interview_round": str(interview_round or "").strip(), "amount_due": max(0, int(amount_due or 0)), "note": str(note or "")[:200], "uploaded_at": _now_iso(), "verification": dict(verification), "fraud_check": fraud_check, "message": fraud_check.get("message") or "", "previousBookingId": fraud_check.get("previousBookingId") or "", "reusedPaymentId": fraud_check.get("reusedPaymentId") or ""}
    with _lock:
        index = _load()
        index["proofs"][proof_id] = entry
        _save(index)
    return dict(entry)


def get_verified_proof(proof_id: str, *, name: str, service_type: str, phone: str = "", candidate_id: str = "", technology: str = "", interview_round: str = "") -> tuple[str, dict] | None:
    token = str(proof_id or "").strip()
    if not token:
        return None
    with _lock:
        entry = dict((_load().get("proofs") or {}).get(token) or {})
    normalized_service = str(service_type or "").strip() or "profile_service"
    if not entry or entry.get("service_type") != normalized_service:
        return None
    if normalized_service == "round_wise":
        from features.candidate_store import candidate_phone_identity
        stored_id = str(entry.get("candidate_id") or "").strip()
        requested_id = str(candidate_id or "").strip()
        stored_phone = candidate_phone_identity(entry.get("phone"))
        requested_phone = candidate_phone_identity(phone)
        id_matches = bool(stored_id and requested_id and stored_id == requested_id)
        phone_matches = bool(stored_phone and stored_phone == requested_phone)
        if (stored_id or stored_phone) and not id_matches and not phone_matches:
            return None
        if not stored_id and not stored_phone and entry.get("name_key") != _name_key(name):
            return None
    elif entry.get("name_key") != _name_key(name):
        return None
    try:
        uploaded_at = datetime.fromisoformat(str(entry.get("uploaded_at") or ""))
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) - uploaded_at.astimezone(timezone.utc) > timedelta(hours=PENDING_PAYMENT_MAX_AGE_HOURS):
        return None
    if not dict(entry.get("verification") or {}).get("booking_eligible"):
        return None
    path = os.path.join(PENDING_PAYMENT_DIR, str(entry.get("filename") or ""))
    return (path, entry) if os.path.isfile(path) else None


def utilized_by(entry: dict) -> str:
    """Which committed booking, if any, has already spent this proof."""
    return str((entry or {}).get("utilized_by_booking_key") or "")


def mark_utilized(proof_ids, *, candidate_id: str = "", booking_key: str = "") -> list[str]:
    """Record that a committed booking consumed these proofs.

    This runs only once the slot is on the candidate row -- verified, not
    assumed. Until then a proof stays reusable, so an upload, a refused
    confirmation, a dropped connection or an abandoned form all leave the payer
    able to try again with the receipt they actually paid with.

    The row attachment is what the fraud check reads and remains the real
    consumption record; this is the pending side of the same fact, kept so the
    lifecycle can be seen and asserted on directly rather than inferred from
    the absence of an attachment somewhere else.
    """
    wanted = [str(pid or "").strip() for pid in (proof_ids or []) if str(pid or "").strip()]
    if not wanted:
        return []
    marked: list[str] = []
    with _lock:
        index = _load()
        proofs = index.get("proofs") or {}
        for pid in wanted:
            entry = proofs.get(pid)
            if not isinstance(entry, dict) or entry.get("utilized_at"):
                continue
            entry["utilized_at"] = _now_iso()
            entry["utilized_by_candidate_id"] = str(candidate_id or "").strip()
            entry["utilized_by_booking_key"] = str(booking_key or "").strip()
            marked.append(pid)
        if marked:
            _save(index)
    return marked


def validate_for_confirmation(
    pending_payment_proof: tuple[str, dict],
    *,
    phone: str = "",
    candidate_id: str = "",
    booking_key: str = "",
) -> dict:
    path, pending = pending_payment_proof
    # A proof already spent by a different committed booking cannot pay for
    # this one. The same booking retrying is not reuse -- it is the same
    # booking -- so its own key passes through.
    spent_by = utilized_by(pending)
    if spent_by and spent_by != str(booking_key or "").strip():
        from features.payment_fraud_detection import PAYMENT_REUSE_BLOCKED_MESSAGE

        raise ValueError(PAYMENT_REUSE_BLOCKED_MESSAGE)
    with open(path, "rb") as handle:
        raw = handle.read()
    from features.payment_fraud_detection import assess_payment_proof

    fraud_check = assess_payment_proof(
        raw,
        dict(pending.get("verification") or {}),
        candidate_id=candidate_id,
        candidate_name=str(pending.get("name_key") or ""),
        candidate_phone=phone or str(pending.get("phone") or ""),
    )
    if fraud_check["decision"] == "rejected":
        raise ValueError(" ".join(fraud_check["reasons"]))
    return fraud_check


def verified_amount(entry: dict) -> int:
    """Rupees one pending proof is verified for.

    Only a booking-eligible proof is worth anything, so an unverified upload
    can never be counted toward what a split payment adds up to.
    """
    verification = dict((entry or {}).get("verification") or {})
    if not verification.get("booking_eligible"):
        return 0
    return max(0, int(verification.get("amount") or 0))


def transaction_identities(entry: dict) -> tuple[str, ...]:
    """Every identifier naming the transaction behind one pending proof.

    Mirrors `payment_receipts._secondary_identity`, so a screenshot that is
    re-uploaded — cropped, renamed, or simply picked twice — collapses onto the
    proof already accepted instead of adding its amount a second time.
    """
    verification = dict((entry or {}).get("verification") or {})
    keys: list[str] = []
    for kind, value in (
        ("utr", verification.get("utr_number")),
        ("transaction_id", verification.get("transaction_id")),
        ("reference", verification.get("reference_number")),
    ):
        folded = "".join(str(value or "").lower().split())
        if len(folded) >= 8:
            keys.append(f"{kind}:{folded}")
    digest = str((entry or {}).get("sha256") or "").strip().lower()
    if digest:
        keys.append(f"screenshot_sha256:{digest}")
    return tuple(keys) or (f"pending_proof:{str((entry or {}).get('id') or '')}",)


def unique_proofs(entries) -> list[dict]:
    """Pending proofs with re-uploads of the same transaction removed."""
    seen: set[str] = set()
    kept: list[dict] = []
    for entry in entries or []:
        keys = transaction_identities(entry)
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        kept.append(entry)
    return kept


def verified_total(entries) -> int:
    """Combined rupees across a split payment, each screenshot counted once."""
    return sum(verified_amount(entry) for entry in unique_proofs(entries))


def duplicates_existing(entry: dict, existing) -> bool:
    """True when this proof is the same transaction as one already accepted."""
    seen: set[str] = set()
    for other in existing or []:
        seen.update(transaction_identities(other))
    return any(key in seen for key in transaction_identities(entry))
