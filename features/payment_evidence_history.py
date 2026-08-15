"""One readable timeline for everything that ever happened to a payment proof.

The history of a proof is scattered by design — amount corrections live on the
ledger payment, quarantine and file-availability changes live beside them,
replacement links live in the evidence manifest, and the proof record on the
candidate carries its own corrections. Each store is right to own its own facts,
but an administrator deciding whether to trust a receipt needs them in one
place, in order.

This module only reads. It never decides anything and never writes.
"""
from __future__ import annotations

from typing import Any

from features import payment_evidence_store, payment_receipts
from features.candidate_attachments import partition_candidate_attachments


def _event(kind: str, at: str, summary: str, **detail: Any) -> dict[str, Any]:
    return {"kind": kind, "at": str(at or ""), "summary": summary,
            **{k: v for k, v in detail.items() if v not in (None, "", [], {})}}


def _payment_for_proof(ledger: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any] | None:
    """Find the ledger payment a proof belongs to, by id then by reference."""
    payments = ledger.get("payments") or []
    payment_id = str(proof.get("payment_id") or "").strip()
    if payment_id:
        match = next(
            (p for p in payments if str(p.get("payment_id")) == payment_id), None
        )
        if match:
            return match
    references = {
        str(proof.get(field) or "").strip().lower()
        for field in ("utr_number", "transaction_id", "reference_number")
        if str(proof.get(field) or "").strip()
    }
    if not references:
        return None
    for payment in payments:
        stored = {str(payment.get("transaction_reference") or "").strip().lower()}
        stored |= {
            str(value or "").strip().lower()
            for value in (payment.get("transaction_references") or {}).values()
        }
        if references & {s for s in stored if s}:
            return payment
    return None


def proof_history(candidate: dict[str, Any], proof_id: str) -> dict[str, Any] | None:
    """Everything known about one proof, newest last."""
    from features import payment_verification_engine as pve

    proofs = partition_candidate_attachments(candidate)["payment_proofs"]
    proof = next((p for p in proofs if str(p.get("id")) == str(proof_id)), None)
    if proof is None:
        return None

    ledger = pve._load_ledger()
    payment = _payment_for_proof(ledger, proof)
    digest = str(proof.get("sha256") or "")
    manifest_record = None
    if digest:
        for record in payment_evidence_store._load_manifest().get("records") or []:
            if record.get("sha256") == digest:
                manifest_record = record
                break

    events: list[dict[str, Any]] = []

    events.append(_event(
        "uploaded",
        proof.get("uploaded_at") or (manifest_record or {}).get("created_at") or "",
        f"Proof {proof.get('id')} uploaded"
        + (f" as {proof.get('original_name')}" if proof.get("original_name") else ""),
        checksum=digest, source=proof.get("source_module"),
        storage_key=(manifest_record or {}).get("storage_key"),
        byte_size=(manifest_record or {}).get("byte_size"),
    ))

    for correction in proof.get("amount_corrections") or []:
        events.append(_event(
            "amount_corrected", correction.get("corrected_at"),
            f"Proof amount corrected from ₹{correction.get('previous_amount'):,} "
            f"to ₹{correction.get('new_amount'):,}",
            actor=correction.get("reviewer"), reason=correction.get("reason"),
            extractor_version=correction.get("extractor_version"),
            previous_verification_state=correction.get("previous_verification_state"),
        ))

    for change in proof.get("file_availability_history") or []:
        events.append(_event(
            "file_availability_changed", change.get("recorded_at"),
            f"Evidence file {change.get('previous')} → {change.get('new')}",
            actor=change.get("reviewer"), reason=change.get("reason"),
        ))

    for swap in proof.get("replacement_history") or []:
        events.append(_event(
            "replaced", swap.get("replaced_at"),
            "Evidence replaced by a fresh upload",
            actor=swap.get("reviewer"), reason=swap.get("reason"),
            previous_checksum=swap.get("previous_checksum"),
            previous_filename=swap.get("previous_filename"),
            previous_verified_amount=swap.get("previous_verified_amount"),
            previous_verification_state=swap.get("previous_verification_state"),
            new_checksum=swap.get("new_checksum"),
        ))

    if payment:
        for correction in payment.get("amount_corrections") or []:
            events.append(_event(
                "ledger_amount_corrected", correction.get("corrected_at"),
                f"Ledger amount corrected from ₹{correction.get('previous_amount'):,} "
                f"to ₹{correction.get('new_amount'):,}",
                actor=correction.get("reviewer"), reason=correction.get("reason"),
                extractor_version=correction.get("extractor_version"),
            ))
        for change in payment.get("quarantine_history") or []:
            events.append(_event(
                "verification_changed", change.get("quarantined_at"),
                f"{change.get('previous_verification_state')} → "
                f"{change.get('new_verification_state')}",
                actor=change.get("reviewer"), reason=change.get("reason"),
                previous_file_availability=change.get("previous_file_availability"),
                new_file_availability=change.get("new_file_availability"),
            ))
        for change in payment.get("file_availability_history") or []:
            events.append(_event(
                "file_availability_changed", change.get("recorded_at"),
                f"Evidence file {change.get('previous')} → {change.get('new')}",
                actor=change.get("reviewer"), reason=change.get("reason"),
            ))
        for item in payment.get("corroborating_evidence") or []:
            events.append(_event(
                "corroborating_evidence", item.get("recorded_at"),
                "Out-of-band evidence recorded (not a system capture)",
                actor=item.get("supplied_by"), source=item.get("source"),
                description=item.get("description"),
                stated_amount=item.get("stated_amount"),
            ))
        if payment.get("admin_disposition"):
            events.append(_event(
                "administrator_disposition", payment.get("updated_at"),
                str(payment.get("admin_disposition")),
                excluded_from_totals=payment.get("excluded_from_totals"),
            ))

    for link in (manifest_record or {}).get("replacement_history") or []:
        events.append(_event(
            "replacement_linked", link.get("linked_at"),
            "Linked as a replacement for earlier evidence",
            actor=link.get("reviewer"), reason=link.get("reason"),
            original_checksum=link.get("original_checksum"),
        ))
    if (manifest_record or {}).get("replaced_by_checksum"):
        events.append(_event(
            "superseded", (manifest_record or {}).get("updated_at"),
            "Superseded by a replacement upload",
            replaced_by_checksum=(manifest_record or {}).get("replaced_by_checksum"),
        ))

    events.sort(key=lambda item: item.get("at") or "")

    availability = payment_receipts.file_availability(proof)
    if digest and manifest_record:
        availability = payment_evidence_store.availability(digest)

    return {
        "proof_id": str(proof.get("id")),
        "stored_in": "proofs" if proof.get("legacy_storage") else "payment_proofs",
        "checksum": digest,
        "original_filename": proof.get("original_name"),
        "verified_amount": proof.get("verified_amount"),
        "verification_state": proof.get("verification_state"),
        "proof_status": payment_receipts.proof_status(proof),
        "file_availability": availability,
        "counts_towards_total": payment_receipts.proof_amount(proof),
        "utr_number": proof.get("utr_number"),
        "transaction_id": proof.get("transaction_id"),
        "receiver_name": proof.get("receiver_name"),
        "payment_id": (payment or {}).get("payment_id"),
        "payment_amount": (
            None if not payment
            else int((payment.get("amount_minor") or 0) // 100)
        ),
        "payment_verification_state": (payment or {}).get("verification_state"),
        "storage_key": (manifest_record or {}).get("storage_key"),
        "replaces_checksum": (manifest_record or {}).get("replaces_checksum"),
        "replaced_by_checksum": (manifest_record or {}).get("replaced_by_checksum"),
        "events": events,
    }
