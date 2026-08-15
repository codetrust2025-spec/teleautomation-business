"""Typed candidate attachment metadata and legacy classification helpers."""
from __future__ import annotations

from enum import Enum
from typing import Any


class AttachmentType(str, Enum):
    PAYMENT_PROOF = "payment_proof"
    SLOT_SCREENSHOT_PROOF = "slot_screenshot_proof"
    PROFILE_PHOTO = "profile_photo"


ATTACHMENT_FIELDS = {
    AttachmentType.PAYMENT_PROOF: "payment_proofs",
    AttachmentType.SLOT_SCREENSHOT_PROOF: "slot_screenshot_proofs",
    AttachmentType.PROFILE_PHOTO: "profile_photo",
}


def parse_attachment_type(value: Any) -> AttachmentType:
    if isinstance(value, AttachmentType):
        return value
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("attachment_type is required")
    try:
        return AttachmentType(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in AttachmentType)
        raise ValueError(f"Invalid attachment_type. Expected one of: {allowed}") from exc


def _combined_context(proof: dict, candidate: dict | None = None) -> str:
    metadata = proof.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    values = [
        proof.get("source_module"),
        proof.get("source_endpoint"),
        proof.get("upload_context"),
        proof.get("note"),
        proof.get("original_name"),
        metadata.get("source_module"),
        metadata.get("source_endpoint"),
        metadata.get("upload_context"),
        metadata.get("kind"),
        (candidate or {}).get("source_module"),
    ]
    return " ".join(str(value or "").strip().lower() for value in values)


def classify_legacy_attachment(
    proof: dict, candidate: dict | None = None
) -> AttachmentType | None:
    """Classify only records with affirmative metadata; ambiguity stays reviewable."""
    if not isinstance(proof, dict):
        return None
    explicit = proof.get("attachment_type")
    if explicit:
        try:
            return parse_attachment_type(explicit)
        except ValueError:
            return None

    metadata = proof.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    context = _combined_context(proof, candidate)

    if proof.get("payment_id") or metadata.get("payment_id"):
        return AttachmentType.PAYMENT_PROOF
    if any(
        proof.get(key) or metadata.get(key)
        for key in ("transaction_id", "utr_number", "ledger_entry_id")
    ):
        return AttachmentType.PAYMENT_PROOF
    if proof.get("booking_id") or metadata.get("booking_id"):
        return AttachmentType.SLOT_SCREENSHOT_PROOF
    if proof.get("slot_screenshot_proof_id") or metadata.get("slot_screenshot_proof_id"):
        return AttachmentType.SLOT_SCREENSHOT_PROOF
    payment_markers = (
        "payment proof",
        "payment receipt",
        "payment_upload",
        "/proofs",
        "whatsapp_payment",
        "candidate_payment",
    )
    slot_markers = (
        "slot screenshot",
        "interview screenshot",
        "interview invite",
        "slot-screenshot",
        "slot_booking",
        "interview_evidence",
    )
    profile_markers = ("profile photo", "profile_photo", "candidate avatar")
    if any(marker in context for marker in profile_markers):
        return AttachmentType.PROFILE_PHOTO
    if any(marker in context for marker in slot_markers):
        return AttachmentType.SLOT_SCREENSHOT_PROOF
    if any(marker in context for marker in payment_markers):
        return AttachmentType.PAYMENT_PROOF
    if (
        candidate
        and int(candidate.get("payment") or 0) > 0
        and str(proof.get("id") or "")
        != str(candidate.get("slot_screenshot_proof_id") or "")
    ):
        # Historical payment uploads had no metadata, but the owning ledger row
        # recorded money. Slot uploads always carried a slot note/id.
        return AttachmentType.PAYMENT_PROOF
    return None


def partition_candidate_attachments(candidate: dict) -> dict:
    """Return isolated typed collections without mutating the source record."""
    payment = list(candidate.get("payment_proofs") or [])
    slots = list(candidate.get("slot_screenshot_proofs") or [])
    profile = candidate.get("profile_photo")
    review = list(candidate.get("attachment_review_queue") or [])

    seen: set[str] = {
        str(item.get("id"))
        for item in payment + slots + ([profile] if isinstance(profile, dict) else [])
        if isinstance(item, dict) and item.get("id")
    }
    for legacy in candidate.get("proofs") or []:
        if not isinstance(legacy, dict):
            continue
        legacy_id = str(legacy.get("id") or "")
        if legacy_id and legacy_id in seen:
            continue
        kind = classify_legacy_attachment(legacy, candidate)
        migrated = {**legacy, "legacy_storage": True}
        if kind:
            migrated["attachment_type"] = kind.value
        if kind == AttachmentType.PAYMENT_PROOF:
            payment.append(migrated)
        elif kind == AttachmentType.SLOT_SCREENSHOT_PROOF:
            slots.append(migrated)
        elif kind == AttachmentType.PROFILE_PHOTO:
            profile = migrated
        else:
            review.append({**migrated, "review_reason": "legacy_attachment_type_uncertain"})
        if legacy_id:
            seen.add(legacy_id)

    return {
        "payment_proofs": payment,
        "slot_screenshot_proofs": slots,
        "profile_photo": profile if isinstance(profile, dict) else None,
        "attachment_review_queue": review,
    }
