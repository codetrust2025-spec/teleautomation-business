"""Durable payment evidence: stored, verified, deduplicated, auditable."""
import os

import pytest

from features import payment_evidence_store as store


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENT_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    return tmp_path / "evidence"


IMAGE = b"\xff\xd8\xff\xe0 pretend jpeg bytes for a payment screenshot"


def test_stored_evidence_is_readable_back(root):
    out = store.store(IMAGE, mime_type="image/jpeg", original_filename="pay.jpg",
                      candidate_id="c1", upload_source="public_slot_payment_proof")
    assert os.path.exists(out["absolute_path"])
    assert store.availability(out["sha256"]) == store.AVAILABLE
    assert store.read(out["sha256"]) == IMAGE


def test_the_manifest_records_everything_needed_to_audit(root):
    out = store.store(IMAGE, mime_type="image/jpeg", original_filename="pay.jpg",
                      candidate_id="c1", proof_id="p1",
                      upload_source="public_slot_payment_proof",
                      transaction_reference="250859628039")
    record = out["record"]
    for field in ("sha256", "storage_key", "mime_type", "byte_size",
                  "original_filename", "candidate_id", "proof_id",
                  "upload_source", "transaction_reference", "created_at"):
        assert record.get(field) not in (None, ""), field
    assert record["byte_size"] == len(IMAGE)


def test_the_same_screenshot_is_never_stored_twice(root):
    first = store.store(IMAGE, mime_type="image/jpeg", candidate_id="c1",
                        upload_source="public_slot_payment_proof")
    second = store.store(IMAGE, mime_type="image/jpeg", candidate_id="c1",
                         upload_source="candidate_payment_proof")
    assert first["sha256"] == second["sha256"]
    assert second["deduplicated"] is True
    report = store.health_report()
    assert report["record_count"] == 1, "one transaction, one stored file"
    assert "candidate_payment_proof" in second["record"]["additional_upload_sources"]


def test_both_upload_paths_share_one_root(root):
    a = store.store(b"public bytes", mime_type="image/png",
                    upload_source="public_slot_payment_proof")
    b = store.store(b"candidate bytes", mime_type="image/png",
                    upload_source="candidate_payment_proof")
    assert a["absolute_path"].startswith(str(root))
    assert b["absolute_path"].startswith(str(root))


def test_empty_evidence_is_refused(root):
    with pytest.raises(ValueError, match="empty"):
        store.store(b"", mime_type="image/jpeg")


def test_a_deleted_file_is_reported_missing(root):
    out = store.store(IMAGE, mime_type="image/jpeg")
    os.remove(out["absolute_path"])
    assert store.availability(out["sha256"]) == store.MISSING_FILE
    with pytest.raises(RuntimeError, match="MISSING_FILE"):
        store.read(out["sha256"])


def test_a_corrupted_file_is_reported_as_a_checksum_mismatch(root):
    out = store.store(IMAGE, mime_type="image/jpeg")
    with open(out["absolute_path"], "wb") as handle:
        handle.write(b"different bytes entirely")
    assert store.availability(out["sha256"]) == store.CHECKSUM_MISMATCH
    with pytest.raises(RuntimeError, match="CHECKSUM_MISMATCH"):
        store.read(out["sha256"])


def test_an_unknown_checksum_is_missing(root):
    assert store.availability("0" * 64) == store.MISSING_FILE


def test_a_replacement_links_to_the_original_without_erasing_it(root):
    original = store.store(b"original screenshot", mime_type="image/jpeg",
                           transaction_reference="250859628039")
    replacement = store.store(b"re-uploaded screenshot", mime_type="image/jpeg",
                              transaction_reference="250859628039")
    linked = store.link_replacement(
        original_checksum=original["sha256"],
        replacement_checksum=replacement["sha256"],
        reviewer="administrator",
        reason="original upload was never retained")
    assert linked["replaces_checksum"] == original["sha256"]
    assert linked["replacement_history"][0]["reviewer"] == "administrator"
    report = store.health_report()
    assert report["record_count"] == 2, "the original record is kept"


def test_a_replacement_must_be_stored_before_it_is_linked(root):
    original = store.store(IMAGE, mime_type="image/jpeg")
    with pytest.raises(ValueError, match="must be stored"):
        store.link_replacement(original_checksum=original["sha256"],
                               replacement_checksum="a" * 64,
                               reviewer="admin", reason="r")


def test_health_report_is_clean_when_everything_is_readable(root):
    store.store(IMAGE, mime_type="image/jpeg", candidate_id="c1")
    report = store.health_report()
    assert report["healthy"] is True
    assert report["problems"] == []
    assert report["availability_counts"][store.AVAILABLE] == 1


def test_health_report_names_the_unreadable_records(root):
    out = store.store(IMAGE, mime_type="image/jpeg", candidate_id="c1",
                      transaction_reference="250859628039")
    os.remove(out["absolute_path"])
    report = store.health_report()
    assert report["healthy"] is False
    problem = report["problems"][0]
    assert problem["availability"] == store.MISSING_FILE
    assert problem["transaction_reference"] == "250859628039"


def test_evidence_survives_a_simulated_release_switch(root, monkeypatch):
    """The root is configured independently of any release directory, so a
    deploy that replaces the code tree leaves evidence untouched."""
    out = store.store(IMAGE, mime_type="image/jpeg")
    # Nothing about the store depends on the current working directory.
    monkeypatch.chdir(str(root.parent))
    assert store.availability(out["sha256"]) == store.AVAILABLE
    assert store.read(out["sha256"]) == IMAGE


def test_storage_key_is_content_addressed_and_sharded(root):
    out = store.store(IMAGE, mime_type="image/jpeg")
    digest = out["sha256"]
    assert out["storage_key"].startswith(digest[:2] + os.sep) or \
        out["storage_key"].startswith(digest[:2] + "/")
    assert digest in out["storage_key"]
