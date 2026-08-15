"""A resume is found by its own id, not by which row the reader was on.

One person usually has several candidate rows - a new one is cloned for each
interview slot - and the resume dialog lists all of their files together. The
link opened from that list names whichever row was on screen, so resolving
only against that row answered "Resume not found" for files that were present.
"""
from __future__ import annotations

import json
import os

import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "RESUMES_DIR", str(tmp_path / "resumes"))
    monkeypatch.setattr(candidate_store, "_load_cache", None, raising=False)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0, raising=False)
    # Identity links live in Postgres, which these tests do not use.
    monkeypatch.setattr(candidate_store, "candidate_identity_ids", lambda cid: [cid])
    return candidate_store


def seed(store, rows):
    payload = {"candidates": [], "updated_at": None}
    for row in rows:
        base = {"stage": "in_progress", "task": "in_progress", "resumes": []}
        base.update(row)
        payload["candidates"].append(base)
    with open(store._FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    store._load_cache = None


def write_file(store, owner_id, rid, body=b"%PDF-1.4 resume"):
    folder = store._resume_dir(owner_id)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{rid}.pdf")
    with open(path, "wb") as handle:
        handle.write(body)
    return path


def entry(rid, owner_id, name="Ravi_Pavankumar_Resume.pdf"):
    return {
        "id": rid, "filename": f"{rid}.pdf", "original_name": name,
        "mime_type": "application/pdf", "size": 15, "note": "",
        "uploaded_at": "2026-07-14T10:06:25+00:00",
        "url": f"/candidates/{owner_id}/resumes/{rid}",
    }


# Pavan Ravi: four rows, resume uploaded against the third, preview opened
# from the fourth. This is the reported production failure.
def pavan(store):
    seed(store, [
        {"id": "11df13b0dd", "name": "Pavan Ravi", "phone": "9876543210"},
        {"id": "6d60fe0381", "name": "Pavan Ravi", "phone": "9876543210"},
        {"id": "88ef89debf", "name": "Pavan Ravi", "phone": "9876543210",
         "resumes": [entry("5e5361fb6c9a", "88ef89debf")]},
        {"id": "e48e99a065", "name": "Pavan Ravi", "phone": "9876543210"},
    ])
    write_file(store, "88ef89debf", "5e5361fb6c9a")


def test_a_resume_opens_from_any_row_of_the_same_person(store):
    pavan(store)
    found = store.get_resume("e48e99a065", "5e5361fb6c9a")
    assert found is not None, "the reported failure: present file reported missing"
    path, item = found
    assert item["original_name"] == "Ravi_Pavankumar_Resume.pdf"
    assert open(path, "rb").read() == b"%PDF-1.4 resume"


def test_it_still_opens_from_the_row_that_owns_it(store):
    pavan(store)
    assert store.get_resume("88ef89debf", "5e5361fb6c9a") is not None


def test_the_owning_row_is_reported_so_callers_can_repair_links(store):
    pavan(store)
    _path, _item, owner = store.find_resume("e48e99a065", "5e5361fb6c9a")
    assert owner == "88ef89debf"


def test_another_person_cannot_reach_it(store):
    pavan(store)
    seed(store, [
        {"id": "88ef89debf", "name": "Pavan Ravi", "phone": "9876543210",
         "resumes": [entry("5e5361fb6c9a", "88ef89debf")]},
        {"id": "stranger01", "name": "Someone Else", "phone": "9000000000"},
    ])
    write_file(store, "88ef89debf", "5e5361fb6c9a")
    assert store.get_resume("stranger01", "5e5361fb6c9a") is None


def test_an_unknown_resume_id_is_not_found(store):
    pavan(store)
    assert store.get_resume("e48e99a065", "no-such-resume") is None


def test_an_unknown_candidate_is_not_found(store):
    pavan(store)
    assert store.get_resume("no-such-candidate", "5e5361fb6c9a") is None


def test_metadata_without_its_file_reports_missing(store):
    seed(store, [{"id": "c1", "name": "Gone File", "phone": "9111111111",
                  "resumes": [entry("rid000000001", "c1")]}])
    # No file written.
    assert store.get_resume("c1", "rid000000001") is None


def test_a_legacy_folder_named_in_the_url_is_still_honoured(store):
    # Profile de-duplication gave the row a new id; the file stayed behind.
    item = entry("rid000000002", "legacyfolder")
    seed(store, [{"id": "newrow0001", "name": "Moved Profile", "phone": "9222222222",
                  "resumes": [item]}])
    write_file(store, "legacyfolder", "rid000000002")
    found = store.get_resume("newrow0001", "rid000000002")
    assert found is not None
    assert "legacyfolder" in found[0]


def test_renaming_changes_the_note_and_never_the_stored_file(store):
    pavan(store)
    before = store.get_resume("e48e99a065", "5e5361fb6c9a")
    updated = store.update_resume_note("e48e99a065", "5e5361fb6c9a", "Latest CV")
    assert updated is not None and updated["note"] == "Latest CV"
    after = store.get_resume("e48e99a065", "5e5361fb6c9a")
    assert after[0] == before[0]
    assert after[1]["filename"] == before[1]["filename"]
    assert os.path.exists(after[0])


def test_deleting_works_from_any_row_of_the_same_person(store):
    pavan(store)
    path = store.get_resume("e48e99a065", "5e5361fb6c9a")[0]
    assert store.delete_resume("e48e99a065", "5e5361fb6c9a") is True
    assert not os.path.exists(path)
    assert store.get_resume("88ef89debf", "5e5361fb6c9a") is None


def test_multiple_versions_each_resolve_to_their_own_file(store):
    seed(store, [{"id": "c1", "name": "Two Files", "phone": "9333333333",
                  "resumes": [entry("ridaaaaaaaa1", "c1", "First.pdf"),
                              entry("ridbbbbbbbb2", "c1", "Second.pdf")]}])
    write_file(store, "c1", "ridaaaaaaaa1", b"first")
    write_file(store, "c1", "ridbbbbbbbb2", b"second")
    assert open(store.get_resume("c1", "ridaaaaaaaa1")[0], "rb").read() == b"first"
    assert open(store.get_resume("c1", "ridbbbbbbbb2")[0], "rb").read() == b"second"


def test_a_release_change_does_not_move_the_storage_root(store):
    # Files live under DATA_DIR, which is shared between releases rather than
    # inside the deployed tree, so a deploy cannot orphan them.
    assert store.RESUMES_DIR.endswith("resumes") or "candidates_resumes" in store.RESUMES_DIR
    assert "releases" not in candidate_store.DATA_DIR
