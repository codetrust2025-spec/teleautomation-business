from services import recruitment_automation as automation
from core import recruitment_mail_store as store


def interview_result(**overrides):
    result = {
        "classification": "interview_confirmed",
        "primary_status": "INTERVIEW_CONFIRMED",
        "evidence": [{"meaning": "INTERVIEW_CONFIRMED", "text": "Interview confirmed"}],
        "requires_manual_review": False,
    }
    result.update(overrides)
    return result


def test_strong_actionable_interview_is_automatically_bookable():
    assert automation.decision_for(interview_result()) is automation.AutomationState.AUTO_BOOK


def test_uncertain_or_legacy_review_is_durable_automatic_retry():
    value = interview_result(requires_manual_review=True, primary_status="MANUAL_REVIEW_REQUIRED")
    projected = automation.apply_decision(value)

    assert projected["automation_decision"] == "AI_RETRY_PENDING"
    assert projected["automation_state"] == "AI_RETRY_PENDING"
    assert projected["requires_manual_review"] is False


def test_claude_auto_ignore_is_preserved_for_marketing_or_webinars():
    value = interview_result(
        classification="not_relevant", primary_status="IGNORED_NOT_OFFER_RELATED",
        automation_decision="AUTO_IGNORE",
    )
    assert automation.decision_for(value) is automation.AutomationState.AUTO_IGNORE


def test_ollama_outage_contract_is_retry_not_ignore():
    value = interview_result(
        primary_status="MANUAL_REVIEW_REQUIRED", validation_status="RETRY_PENDING",
        requires_manual_review=True,
    )
    assert automation.decision_for(value) is automation.AutomationState.AI_RETRY_PENDING


def test_booking_outcomes_never_claim_an_unpersisted_slot_as_auto_booked():
    assert automation.outcome_for({"status": "Auto Booked"}) is automation.AutomationState.AUTO_BOOKED
    assert automation.outcome_for({"status": "Rescheduled"}) is automation.AutomationState.AUTO_RESCHEDULED
    assert automation.outcome_for({"status": "Cancelled"}) is automation.AutomationState.AUTO_CANCELLED
    assert automation.outcome_for({"status": "Blocked", "failure_code": "BOOKING_NOT_PERSISTED"}) is automation.AutomationState.AI_RETRY_PENDING


def test_exact_duplicate_and_stale_replay_are_terminally_ignored_not_retried():
    assert automation.outcome_for({"status": "Duplicate Ignored", "failure_code": "DUPLICATE_BOOKING"}) is automation.AutomationState.AUTO_IGNORE
    assert automation.outcome_for({"status": "Blocked", "failure_code": "STALE_INTERVIEW_EVENT"}) is automation.AutomationState.AUTO_IGNORE


class _Cursor:
    def execute(self, *_args, **_kwargs):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def cursor(self):
        return _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_mailbox_only_terminal_states_get_an_automation_ledger_entry(monkeypatch):
    transitions = []
    monkeypatch.setattr(store, "get_connection", lambda: _Connection())
    monkeypatch.setattr(
        store, "record_automation_state",
        lambda **kwargs: transitions.append(kwargs),
    )

    store.mark_message_status("mail-1", "IGNORED_NOT_OFFER_RELATED", reason="MARKETING")
    store.mark_message_status("mail-2", "AI_RETRY_PENDING", reason="OLLAMA_UNAVAILABLE")

    assert transitions == [
        {
            "mailbox_message_id": "mail-1", "event_id": None,
            "state": "AUTO_IGNORE", "reason": "MARKETING",
            "details": {"source_status": "IGNORED_NOT_OFFER_RELATED"},
        },
        {
            "mailbox_message_id": "mail-2", "event_id": None,
            "state": "AI_RETRY_PENDING", "reason": "OLLAMA_UNAVAILABLE",
            "details": {"source_status": "AI_RETRY_PENDING"},
        },
    ]
