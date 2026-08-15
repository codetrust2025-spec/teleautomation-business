"""Safe, idempotent migration from candidate.proofs to typed attachments."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from features.candidate_attachments import (
    AttachmentType,
    classify_legacy_attachment,
)


def migrate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    migrated = deepcopy(payload)
    stats = {"candidates": 0, "payment": 0, "slot": 0, "profile": 0, "review": 0}
    for row in migrated.get("candidates") or []:
        legacy = list(row.get("proofs") or [])
        if not legacy and int(row.get("attachment_schema_version") or 0) >= 2:
            continue

        payment = list(row.get("payment_proofs") or [])
        slots = list(row.get("slot_screenshot_proofs") or [])
        profile = row.get("profile_photo") if isinstance(row.get("profile_photo"), dict) else None
        review = list(row.get("attachment_review_queue") or [])
        known = {
            str(item.get("id"))
            for item in payment + slots + ([profile] if profile else []) + review
            if isinstance(item, dict) and item.get("id")
        }

        for proof in legacy:
            if not isinstance(proof, dict):
                continue
            pid = str(proof.get("id") or "")
            if pid and pid in known:
                continue
            kind = classify_legacy_attachment(proof, row)
            entry = {**proof, "legacy_storage": True}
            if kind:
                entry["attachment_type"] = kind.value
            if kind == AttachmentType.PAYMENT_PROOF:
                payment.append(entry)
                stats["payment"] += 1
            elif kind == AttachmentType.SLOT_SCREENSHOT_PROOF:
                slots.append(entry)
                stats["slot"] += 1
            elif kind == AttachmentType.PROFILE_PHOTO:
                profile = entry
                stats["profile"] += 1
            else:
                review.append(
                    {
                        **entry,
                        "review_reason": "legacy_attachment_type_uncertain",
                    }
                )
                stats["review"] += 1
            if pid:
                known.add(pid)

        row["payment_proofs"] = payment
        row["slot_screenshot_proofs"] = slots
        row["profile_photo"] = profile
        row["attachment_review_queue"] = review
        row["attachment_schema_version"] = 2
        row.pop("proofs", None)
        stats["candidates"] += 1
    return migrated, stats


def run(*, apply: bool = False) -> dict[str, int]:
    from features import candidate_store

    payload = candidate_store._load(force=True)
    migrated, stats = migrate_payload(payload)
    if apply and stats["candidates"]:
        candidate_store._save(migrated)
    return stats
