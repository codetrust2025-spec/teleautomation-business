import core.ocr_policy as policy
import features.ollama_payment_extract as payment_extract


def test_ocr_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("OCR_ENABLED", raising=False)
    assert policy.ocr_enabled() is True


def test_global_ocr_kill_switch_accepts_common_false_values(monkeypatch):
    for value in ("false", "0", "off", "disabled", "no"):
        monkeypatch.setenv("OCR_ENABLED", value)
        assert policy.ocr_enabled() is False


def test_payment_extraction_bypasses_ocr_when_globally_disabled(monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "false")
    monkeypatch.setattr(payment_extract, "_is_ollama_available", lambda: True)

    def fail_if_called(_image):
        raise AssertionError("OCR must not run while globally disabled")

    monkeypatch.setattr(payment_extract, "_run_tesseract_ocr", fail_if_called)
    monkeypatch.setattr(
        payment_extract,
        "_call_vision_model",
        lambda *_args, **_kwargs: (
            '{"amount":5000,"receiver_name":"J Ravinder",'
            '"receiver_upi_id":"company@upi","utr_number":"459877656303",'
            '"status":"success","confidence_score":98,"is_payment_screenshot":true}'
        ),
    )

    result = payment_extract.extract_payment_with_ollama(b"image", "image/jpeg")

    assert result["extraction_method"] == "vision"
    assert result["receiver_upi_id"] == "company@upi"


# ── persistence, provenance and audit ────────────────────────────────────────
# The switch used to be environment-only, so changing it meant a redeploy and
# left no record of who changed it.

import json

import pytest


@pytest.fixture
def isolated_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("OCR_POLICY_FILE", str(tmp_path / "ocr_policy.json"))
    monkeypatch.delenv("OCR_ENABLED", raising=False)
    return tmp_path / "ocr_policy.json"


def test_saved_setting_overrides_the_environment(isolated_policy, monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "true")
    policy.set_ocr_enabled(False, actor="admin")
    assert policy.ocr_enabled() is False
    status = policy.status()
    assert status["source"] == "admin"
    assert status["env_default"] is True


def test_setting_is_persisted_to_disk(isolated_policy):
    policy.set_ocr_enabled(False, actor="admin")
    stored = json.loads(isolated_policy.read_text(encoding="utf-8"))
    assert stored["enabled"] is False
    assert policy.ocr_enabled() is False


def test_toggling_back_on_is_persisted(isolated_policy):
    policy.set_ocr_enabled(False, actor="admin")
    policy.set_ocr_enabled(True, actor="admin")
    assert policy.ocr_enabled() is True
    assert policy.processing_mode() == "ocr+ai"


def test_mode_label_tracks_the_switch(isolated_policy):
    policy.set_ocr_enabled(False, actor="admin")
    assert policy.status()["mode"] == "ai"
    policy.set_ocr_enabled(True, actor="admin")
    assert policy.status()["mode"] == "ocr+ai"


def test_every_change_records_actor_time_and_both_values(isolated_policy):
    policy.set_ocr_enabled(False, actor="alice", source_ip="10.0.0.1")
    entries = policy.audit_log()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actor"] == "alice"
    assert entry["previous"] is True
    assert entry["new"] is False
    assert entry["source_ip"] == "10.0.0.1"
    assert entry["at"]


def test_audit_is_newest_first_and_keeps_history(isolated_policy):
    policy.set_ocr_enabled(False, actor="alice")
    policy.set_ocr_enabled(True, actor="bob")
    entries = policy.audit_log()
    assert [e["actor"] for e in entries] == ["bob", "alice"]
    assert entries[0]["previous"] is False and entries[0]["new"] is True


def test_audit_is_trimmed_so_the_file_cannot_grow_without_bound(isolated_policy):
    for index in range(policy._MAX_AUDIT_ENTRIES + 10):
        policy.set_ocr_enabled(index % 2 == 0, actor=f"admin-{index}")
    stored = json.loads(isolated_policy.read_text(encoding="utf-8"))
    assert len(stored["audit"]) == policy._MAX_AUDIT_ENTRIES


# ── the promise that matters: OFF means off everywhere ───────────────────────

@pytest.mark.parametrize("module_name", [
    "features.ollama_invite_extract",
    "features.ollama_payment_extract",
    "features.ollama_resume_extract",
    "features.payment_proof_validator",
    "features.slot_screenshot_parse",
])
def test_every_tesseract_entry_point_consults_the_policy(module_name):
    """A module that runs Tesseract without asking is a hidden fallback."""
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    if "pytesseract" not in source:
        pytest.skip(f"{module_name} no longer runs OCR directly")
    assert "ocr_enabled" in source, (
        f"{module_name} runs Tesseract without consulting ocr_policy.ocr_enabled()"
    )
