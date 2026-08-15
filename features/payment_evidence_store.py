"""Durable storage for payment evidence.

Financial evidence has to outlive the request that created it. The public
upload path recorded a ledger entry — checksum, extraction, UTR and all — while
the image itself only ever existed in memory for the duration of the request,
so three payments ended up with perfect metadata and no screenshot to re-read.
Metadata about a file is not the file.

Files are content-addressed by SHA-256 under a single managed root. Two
consequences follow for free: re-uploading the same screenshot writes nothing
new and cannot create a second credit, and a stored file can always be checked
against the name it is filed under.

The root lives under DATA_DIR, which deployment symlinks to a location shared
across releases, so evidence survives deploys, restarts and rollbacks.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.config import DATA_DIR

_lock = RLock()

AVAILABLE = "AVAILABLE"
MISSING_FILE = "MISSING_FILE"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
UNREADABLE = "UNREADABLE"
ARCHIVED = "ARCHIVED"

_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic",
    "image/heif": ".heif", "application/pdf": ".pdf",
}


def evidence_root() -> str:
    return os.environ.get(
        "PAYMENT_EVIDENCE_ROOT", os.path.join(DATA_DIR, "payment_evidence")
    )


def _manifest_file() -> str:
    return os.path.join(evidence_root(), "manifest.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_key(digest: str, mime_type: str = "", original_filename: str = "") -> str:
    """Content-addressed path, sharded so one directory never holds everything."""
    extension = _EXTENSIONS.get((mime_type or "").lower().split(";")[0].strip(), "")
    if not extension and "." in (original_filename or ""):
        extension = "." + original_filename.rsplit(".", 1)[1].lower()[:5]
    return os.path.join(digest[:2], f"{digest}{extension or '.bin'}")


def absolute_path(key: str) -> str:
    return os.path.join(evidence_root(), key)


def _load_manifest() -> dict[str, Any]:
    try:
        with open(_manifest_file(), encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("records", [])
    return data


def _save_manifest(data: dict[str, Any]) -> None:
    root = evidence_root()
    os.makedirs(root, exist_ok=True)
    data["updated_at"] = _now()
    tmp = _manifest_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, _manifest_file())


def store(
    data: bytes,
    *,
    mime_type: str,
    original_filename: str = "",
    candidate_id: str = "",
    proof_id: str = "",
    upload_source: str = "",
    replaces_checksum: str = "",
    transaction_reference: str = "",
) -> dict[str, Any]:
    """Write evidence durably and confirm it landed before reporting success.

    Raises if the file cannot be read back and verified — a caller must never
    be told an upload succeeded when the bytes are not actually retrievable,
    which is the failure this module exists to prevent.
    """
    if not data:
        raise ValueError("Refusing to store empty payment evidence")
    digest = checksum(data)
    key = storage_key(digest, mime_type, original_filename)
    path = absolute_path(key)

    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        already_present = os.path.exists(path)
        if not already_present:
            tmp = path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)

        # Read it back. Reporting success on a write we have not confirmed is
        # exactly how the original evidence went missing.
        try:
            with open(path, "rb") as handle:
                written = handle.read()
        except OSError as exc:
            raise RuntimeError(f"Stored evidence is not readable at {key}: {exc}")
        if checksum(written) != digest:
            raise RuntimeError(f"Stored evidence checksum mismatch at {key}")

        manifest = _load_manifest()
        record = next(
            (r for r in manifest["records"] if r.get("sha256") == digest), None
        )
        if record is None:
            record = {
                "sha256": digest,
                "storage_key": key,
                "mime_type": mime_type,
                "byte_size": len(data),
                "original_filename": original_filename,
                "candidate_id": candidate_id,
                "proof_id": proof_id,
                "upload_source": upload_source,
                "transaction_reference": transaction_reference,
                "replaces_checksum": replaces_checksum,
                "created_at": _now(),
                "availability": AVAILABLE,
            }
            manifest["records"].append(record)
        else:
            # Same bytes seen again. Keep the original record and note the
            # additional context rather than filing a second copy.
            for field, value in (
                ("candidate_id", candidate_id), ("proof_id", proof_id),
                ("transaction_reference", transaction_reference),
            ):
                if value and not record.get(field):
                    record[field] = value
            sources = record.setdefault("additional_upload_sources", [])
            if upload_source and upload_source != record.get("upload_source"):
                if upload_source not in sources:
                    sources.append(upload_source)
            record["availability"] = AVAILABLE
            record["updated_at"] = _now()
        _save_manifest(manifest)

    return {
        "sha256": digest,
        "storage_key": key,
        "absolute_path": path,
        "byte_size": len(data),
        "deduplicated": already_present,
        "record": dict(record),
    }


def availability(digest: str) -> str:
    """Whether the evidence behind a checksum can still be read."""
    manifest = _load_manifest()
    record = next(
        (r for r in manifest["records"] if r.get("sha256") == digest), None
    )
    if record is None:
        return MISSING_FILE
    if record.get("availability") == ARCHIVED:
        return ARCHIVED
    path = absolute_path(str(record.get("storage_key") or ""))
    if not os.path.exists(path):
        return MISSING_FILE
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return UNREADABLE
    return AVAILABLE if checksum(data) == digest else CHECKSUM_MISMATCH


def read(digest: str) -> bytes:
    state = availability(digest)
    if state != AVAILABLE:
        raise RuntimeError(f"Evidence {digest[:12]} is {state}")
    manifest = _load_manifest()
    record = next(r for r in manifest["records"] if r.get("sha256") == digest)
    with open(absolute_path(str(record["storage_key"])), "rb") as handle:
        return handle.read()


def link_replacement(
    *, original_checksum: str, replacement_checksum: str, reviewer: str, reason: str
) -> dict[str, Any]:
    """Record that one upload stands in for evidence that can no longer be read.

    The original record is kept. A replacement is a second capture of the same
    transaction, not a correction of the first, and conflating them would lose
    the fact that the first was ever missing.
    """
    with _lock:
        manifest = _load_manifest()
        replacement = next(
            (r for r in manifest["records"] if r.get("sha256") == replacement_checksum),
            None,
        )
        if replacement is None:
            raise ValueError("The replacement must be stored before it can be linked")
        replacement["replaces_checksum"] = original_checksum
        replacement.setdefault("replacement_history", []).append({
            "linked_at": _now(),
            "original_checksum": original_checksum,
            "reviewer": reviewer,
            "reason": reason,
        })
        original = next(
            (r for r in manifest["records"] if r.get("sha256") == original_checksum),
            None,
        )
        if original is not None:
            original["replaced_by_checksum"] = replacement_checksum
            original["updated_at"] = _now()
        _save_manifest(manifest)
        return dict(replacement)


def health_report() -> dict[str, Any]:
    """Storage audit: what is on record, and what can still be read."""
    manifest = _load_manifest()
    records = manifest.get("records") or []
    states: dict[str, int] = {}
    problems: list[dict[str, Any]] = []
    total_bytes = 0
    for record in records:
        state = availability(str(record.get("sha256") or ""))
        states[state] = states.get(state, 0) + 1
        total_bytes += int(record.get("byte_size") or 0)
        if state != AVAILABLE:
            problems.append({
                "sha256": record.get("sha256"),
                "storage_key": record.get("storage_key"),
                "candidate_id": record.get("candidate_id"),
                "transaction_reference": record.get("transaction_reference"),
                "upload_source": record.get("upload_source"),
                "availability": state,
            })
    return {
        "evidence_root": evidence_root(),
        "record_count": len(records),
        "total_bytes": total_bytes,
        "availability_counts": states,
        "problems": problems,
        "healthy": not problems,
    }
