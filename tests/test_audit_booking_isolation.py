"""Proof that the Ollama mail audit cannot affect interview auto-booking.

Each test corresponds to one isolation guarantee the operator required. They
are deliberately structural where behaviour cannot be exercised offline: an
import that does not exist cannot be called at runtime, and a table that is
never named cannot be written.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_audit_ai as audit_ai  # noqa: E402
from services import interview_auto_booking as booking  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
AUDIT_SOURCES = (
    "core/recruitment_audit_ai.py",
    "core/recruitment_mail_audit.py",
    "core/recruitment_mail_audit_store.py",
)


def source(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


# ── 1. Separate feature flag ─────────────────────────────────────────────────

def test_the_audit_has_its_own_flag():
    assert audit_ai.FEATURE_FLAG == "AI_MAIL_AUDIT_ENABLED"


def test_the_audit_never_reads_the_booking_flag():
    """Naming the flag in a comment is fine; reading it is not."""
    for name in AUDIT_SOURCES:
        text = source(name)
        for read in ('getenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED"',
                     "getenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED'",
                     'environ["AI_INTERVIEW_AUTO_BOOKING_ENABLED"',
                     "environ['AI_INTERVIEW_AUTO_BOOKING_ENABLED'"):
            assert read not in text, f"{name} reads the booking flag"


def test_disabling_the_audit_does_not_disable_auto_booking(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "false")
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    assert audit_ai.enabled() is False
    # Booking validation still succeeds with the audit switched off.
    booking.validate_ai_for_booking({
        "classification_source": "OLLAMA", "ai_validation_status": "VALIDATED",
        "confidence": 0.96, "requires_manual_review": False,
        "interview": {"date": "2099-01-01", "time": "03:00 PM", "timezone": "Asia/Kolkata"},
    }, "interview_confirmed")


def test_disabling_auto_booking_does_not_disable_the_audit(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "false")
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    assert audit_ai.enabled() is True


def test_the_audit_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("AI_MAIL_AUDIT_ENABLED", raising=False)
    assert audit_ai.enabled() is False


# ── 2. No booking code is reachable from the audit ───────────────────────────

FORBIDDEN_CALLS = (
    "execute_auto_booking", "execute_manual_approved_booking",
    "assign_interview_slot", "cancel_interview_slot", "reschedule_interview_slot",
    "validate_ai_for_booking", "validate_manual_approval_for_booking",
)


@pytest.mark.parametrize("name", AUDIT_SOURCES)
def test_no_audit_module_references_a_booking_function(name):
    text = source(name)
    for call in FORBIDDEN_CALLS:
        assert call not in text, f"{name} references {call}"


@pytest.mark.parametrize("name", AUDIT_SOURCES)
def test_no_audit_module_imports_the_booking_module(name):
    text = source(name)
    for statement in ("from services.interview_auto_booking",
                     "from services import interview_auto_booking",
                     "import services.interview_auto_booking"):
        assert statement not in text, name


def test_the_audit_ai_module_imports_nothing_from_services_at_module_level():
    text = source("core/recruitment_audit_ai.py")
    header = text.split("# ── Capacity deference", 1)[0]
    assert "from services" not in header


# ── 3. Separate storage ──────────────────────────────────────────────────────

BOOKING_TABLES = (
    "interview_auto_booking_audit", "interview_mail_analyses",
    "mailbox_sync_jobs", "candidate_job_status", "candidate_status_history",
)


def test_the_audit_ai_never_writes_a_booking_table():
    text = source("core/recruitment_audit_ai.py")
    for table in BOOKING_TABLES:
        for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
            assert f"{verb} {table}" not in text, f"audit writes {table}"


def test_the_audit_ai_owns_its_tables():
    migration = source("core/migrations/022_recruitment_mail_audit_ai.sql")
    for table in ("mail_audit_ai_queue", "mail_audit_ai_results", "mail_audit_ai_log"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    # And creates or alters nothing belonging to booking.
    for table in BOOKING_TABLES:
        assert f"ALTER TABLE {table}" not in migration
        assert f"CREATE TABLE IF NOT EXISTS {table}" not in migration


def test_the_audit_queue_is_not_the_booking_queue():
    text = source("core/recruitment_audit_ai.py")
    assert "mail_audit_ai_queue" in text
    # mailbox_sync_jobs may only be READ, to know whether to yield.
    assert "UPDATE mailbox_sync_jobs" not in text
    assert "INSERT INTO mailbox_sync_jobs" not in text


def test_the_audit_cache_key_is_namespaced_to_the_audit_prompt():
    key = audit_ai.cache_key({"provider_message_id": "m1", "outcome": "SHORTLISTED",
                              "content_signature": "sig"})
    other = audit_ai.cache_key({"provider_message_id": "m1", "outcome": "SHORTLISTED",
                                "content_signature": "different"})
    assert key != other
    assert audit_ai.AUDIT_PROMPT_NAME == "recruitment_mail_audit_second_opinion_v1"


def test_the_audit_prompt_is_not_a_booking_prompt():
    text = source("core/recruitment_audit_ai.py")
    assert "recruitment_email_status_extraction_v3" not in text
    assert "read-only reviewer" in audit_ai.AUDIT_SYSTEM_PROMPT
    assert "cannot book" in audit_ai.AUDIT_SYSTEM_PROMPT


# ── 4. Live processing always has priority ───────────────────────────────────

@contextmanager
def fake_live(monkeypatch, *, sync_jobs=0, ai_backlog=0, ingestion=0):
    monkeypatch.setattr(audit_ai, "live_pipeline_busy", lambda: {
        "busy": bool(sync_jobs or ai_backlog or ingestion),
        "sync_jobs": sync_jobs, "ai_backlog": ai_backlog, "ingestion": ingestion,
    })
    yield


def test_the_audit_yields_while_a_sync_job_is_queued(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    with fake_live(monkeypatch, sync_jobs=1):
        gate = audit_ai.may_run()
    assert gate["allowed"] is False
    assert "yields" in gate["reason"]


def test_the_audit_yields_while_the_live_ai_queue_has_due_work(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    with fake_live(monkeypatch, ai_backlog=3):
        assert audit_ai.may_run()["allowed"] is False


def test_only_due_retries_count_as_live_work():
    """Messages parked in exponential backoff are not competing for the node.

    Production held 56 messages in AI_RETRY_PENDING with retry counts up to 77,
    every one of them parked hours ahead. Counting those as live work blocked
    the audit permanently while taking no capacity from anything.
    """
    source = (REPO / "core" / "recruitment_audit_ai.py").read_text(encoding="utf-8")
    body = source.split("def live_pipeline_busy(", 1)[1].split("\ndef ", 1)[0]
    assert "ai_retry_after IS NULL OR ai_retry_after <= now()" in body
    # The parked count is reported but must not make the pipeline "busy".
    assert "ai_in_backoff" in body
    busy_line = [line for line in body.splitlines() if line.strip().startswith("busy = ")]
    assert busy_line and "ai_deferred" not in busy_line[0]


def test_the_audit_yields_while_gmail_ingestion_is_pending(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    with fake_live(monkeypatch, ingestion=5):
        assert audit_ai.may_run()["allowed"] is False


def test_the_audit_runs_only_when_the_pipeline_is_idle_and_ollama_is_up(monkeypatch):
    import core.ai_gateway  # noqa: F401  (ensure the module is importable to patch)
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    monkeypatch.setattr("core.ai_gateway.health",
                        lambda **kw: {"endpoint_reachable": True, "model_available": True})
    with fake_live(monkeypatch):
        assert audit_ai.may_run()["allowed"] is True


def test_an_unavailable_ollama_leaves_audit_jobs_pending(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    monkeypatch.setattr("core.ai_gateway.health",
                        lambda **kw: {"endpoint_reachable": False, "model_available": False})
    with fake_live(monkeypatch):
        gate = audit_ai.may_run()
    assert gate["allowed"] is False
    assert "pending" in gate["reason"]


def test_process_pending_returns_without_work_when_deferred(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_ENABLED", "true")
    claimed = []
    monkeypatch.setattr(audit_ai, "claim", lambda limit=1: claimed.append(limit) or [])
    with fake_live(monkeypatch, sync_jobs=1):
        result = audit_ai.process_pending()
    assert result["deferred"] is True
    assert result["ran"] == 0
    # Nothing was claimed, so no capacity was taken from the live pipeline.
    assert claimed == []


def test_audit_concurrency_is_capped_low(monkeypatch):
    monkeypatch.setenv("AI_MAIL_AUDIT_CONCURRENCY", "50")
    assert audit_ai.max_concurrency() == 2
    monkeypatch.setenv("AI_MAIL_AUDIT_CONCURRENCY", "0")
    assert audit_ai.max_concurrency() == 1
    monkeypatch.delenv("AI_MAIL_AUDIT_CONCURRENCY", raising=False)
    assert audit_ai.max_concurrency() == 1


# ── 5. Failure containment ───────────────────────────────────────────────────

def test_a_malformed_model_response_never_becomes_an_outcome(monkeypatch):
    """A non-object answer is rejected and the finding is left untouched."""
    stored = []
    monkeypatch.setattr(audit_ai, "_finding_for_job", lambda fid: {
        "id": "f1", "provider_message_id": "m1", "outcome": "SHORTLISTED",
        "content_signature": "s", "body_text": "x", "rationale": "r",
        "subject": "s", "sender_email": "a@b.example", "sender_domain": "b.example",
        "company_name": None, "company_domain": None, "job_title": None,
        "received_at": None,
    })
    monkeypatch.setattr(audit_ai, "cached_result", lambda key: None)
    monkeypatch.setattr(audit_ai, "_store_result",
                        lambda *a, **k: stored.append(a) or {})
    finished = []
    monkeypatch.setattr(audit_ai, "_finish",
                        lambda job_id, status, **kw: finished.append(status))
    monkeypatch.setattr("core.ai_gateway.chat_structured",
                        lambda **kw: "not-a-dict")

    result = audit_ai.review_one({"id": "j1", "finding_id": "f1"})
    assert result["status"] == "FAILED"
    assert stored == []
    assert finished == [audit_ai.QUEUE_FAILED]


def test_a_gateway_exception_is_contained(monkeypatch):
    monkeypatch.setattr(audit_ai, "_finding_for_job", lambda fid: {
        "id": "f1", "provider_message_id": "m1", "outcome": "SHORTLISTED",
        "content_signature": "s", "body_text": "x", "rationale": "r",
        "subject": "s", "sender_email": "a@b.example", "sender_domain": "b.example",
        "company_name": None, "company_domain": None, "job_title": None,
        "received_at": None,
    })
    monkeypatch.setattr(audit_ai, "cached_result", lambda key: None)
    finished = []
    monkeypatch.setattr(audit_ai, "_finish",
                        lambda job_id, status, **kw: finished.append(status))

    def boom(**kwargs):
        raise RuntimeError("node offline")

    monkeypatch.setattr("core.ai_gateway.chat_structured", boom)
    result = audit_ai.review_one({"id": "j1", "finding_id": "f1"})
    assert result["status"] == "FAILED"
    assert "node offline" in result["error"]


def test_audit_retries_only_touch_the_audit_queue():
    text = source("core/recruitment_audit_ai.py")
    retry = text.split("def _finish(", 1)[1].split("\ndef ", 1)[0]
    assert "mail_audit_ai_queue" in retry
    for table in ("mailbox_sync_jobs", "gmail_message_ingestion_queue",
                  "mailbox_messages", "interview_auto_booking_audit"):
        assert table not in retry, f"audit retry touches {table}"


def test_the_worker_treats_audit_failure_as_non_fatal():
    text = source("workers/recruitment_mail_worker.py")
    body = text.split("def process_audit_ai(", 1)[1].split("\n    def ", 1)[0]
    assert "except Exception" in body
    assert "booking is unaffected" in body


def test_the_worker_only_schedules_audit_work_when_no_sync_job_is_running():
    text = source("workers/recruitment_mail_worker.py")
    assert "live_idle=not self._jobs" in text
    assert "if live_idle and self._audit_ai_task is None" in text
    assert "if live_idle and self._audit_task is None" in text


# ── 6. Read-only guarantee ───────────────────────────────────────────────────

def test_the_audit_ai_cannot_change_candidate_status():
    text = source("core/recruitment_audit_ai.py")
    assert "candidate_job_status" not in text
    assert "candidate_status_history" not in text


def test_the_audit_ai_never_touches_gmail():
    """Prose about not modifying Gmail is fine; a Gmail client is not."""
    text = source("core/recruitment_audit_ai.py")
    for token in ("GmailMailboxProvider", "gmail_mailbox_provider",
                  "gmail.googleapis", "messages/send", "messages/modify",
                  "messages/trash", "batchModify"):
        assert token not in text, f"audit references {token}"


def test_the_audit_result_is_advisory_only():
    """It records a suggestion; it does not overwrite the rule engine."""
    text = source("core/recruitment_audit_ai.py")
    store_body = text.split("def _store_result(", 1)[1].split("\ndef ", 1)[0]
    assert "mail_audit_ai_results" in store_body
    assert "UPDATE mail_outcome_audit_findings" not in store_body
    assert "suggested_outcome" in store_body


def test_the_audit_never_approves_an_outcome_automatically():
    text = source("core/recruitment_audit_ai.py")
    assert "approve_outcome" not in text
    assert "mail_outcome_audit_approvals" not in text
