"""Contradictory calendar relevance resolves to BOOK, IGNORE, or automatic retry."""

from __future__ import annotations

import inspect

import pytest

from services import recruitment_mail_agent as agent


def relevance(decision, kind):
    return {"decision": decision, "message_kind": kind, "confidence": 0.96}


class TestTheThreeAutomatedVerdicts:
    def test_an_agreed_hiring_process_is_booked(self):
        assert agent.calendar_invite_verdict(relevance("ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) == "BOOK"

    @pytest.mark.parametrize("decision,kind", [
        ("ESTABLISHED", "MARKETING_OR_TRAINING"),
        ("ESTABLISHED", "GENERAL"),
        ("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
    ])
    def test_unproven_contradictions_retry(self, decision, kind):
        assert agent.calendar_invite_verdict(relevance(decision, kind)) == "RETRY"

    @pytest.mark.parametrize("kind", ["PUBLIC_EVENT", "NEWSLETTER", "MARKETING_OR_TRAINING", "UNKNOWN"])
    def test_confident_non_candidate_is_ignored(self, kind):
        assert agent.calendar_invite_verdict(relevance("NOT_ESTABLISHED", kind)) == "IGNORE"


class TestNoReviewBranchRemains:
    def test_retries_are_durable_and_human_free(self):
        source = inspect.getsource(agent.process_message)
        block = source[source.index('if verdict == "RETRY"'):source.index('elif verdict == "IGNORE"')]
        assert "AI_RETRY_PENDING" in block
        assert "MANUAL_REVIEW_REQUIRED" not in block
        assert "requires_manual_review=True" not in block

    def test_gateway_failure_retries_rather_than_ignores(self):
        source = inspect.getsource(agent.process_message)
        failure = source.index("CALENDAR_INTENT_UNAVAILABLE")
        assert "AI_RETRY_PENDING" in source[failure - 400:failure + 400]
