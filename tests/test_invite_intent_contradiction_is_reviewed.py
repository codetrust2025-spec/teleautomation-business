"""A contradictory intent answer is a question for an operator, not a rejection.

The calendar gate required the relevance model to say ESTABLISHED *and* to name
the kind RECIPIENT_HIRING_PROCESS. Those are not the same question. The prompt
asks for `decision` -- "does the source tie this recipient to a real hiring
process" -- and it already says marketing, training, webinars and public events
are NOT_ESTABLISHED. `message_kind` is a label describing the mail.

Requiring both to agree dropped real interviews. Checked against production,
three genuine cancellations for named candidates came back ESTABLISHED with a
contradicting kind and were silently discarded:

    Canceled: L1 Interview_Gangadhar Nagarale      ESTABLISHED / MARKETING_OR_TRAINING
    Canceled: Gopichand - Round 1 - System Admin   ESTABLISHED / GENERAL
    Canceled: [Send Secure]Altisource Discussion   ESTABLISHED / MARKETING_OR_TRAINING

each quoting the candidate's own interview line as its evidence. A cancellation
carries its meaning in the subject and the .ics -- the bodies are disclaimer and
stylesheet boilerplate -- so the model has little to label the mail from, and
mislabels it while judging it correctly.

Nothing is loosened for webinars. Every marketing sample checked in production
answered NOT_ESTABLISHED, which still ignores them here.
"""

from __future__ import annotations

import inspect

import pytest

from services import recruitment_mail_agent as agent


def relevance(decision, kind):
    return {
        "decision": decision, "message_kind": kind, "confidence": 0.96,
        "evidence": [{"source": "EMAIL_SUBJECT", "text": "L1 Interview"}],
        "reason": "checked against production",
    }


# What production actually returned for the six cancellations, and for the
# marketing mails that must stay out.
GENUINE_KEPT = [("ESTABLISHED", "RECIPIENT_HIRING_PROCESS")]
GENUINE_CONTRADICTORY = [
    ("ESTABLISHED", "MARKETING_OR_TRAINING"),   # eOne L1, Altisource
    ("ESTABLISHED", "GENERAL"),                 # Skillmine round one
]
MARKETING = [
    ("NOT_ESTABLISHED", "PUBLIC_EVENT"),        # Zoom workshop, Yocket, Impacteers
    ("NOT_ESTABLISHED", "NEWSLETTER"),          # Naukri bootcamp
    ("NOT_ESTABLISHED", "MARKETING_OR_TRAINING"),  # Talent500, GraphoTherapy
    ("NOT_ESTABLISHED", "UNKNOWN"),
]


class TestTheThreeVerdicts:
    @pytest.mark.parametrize("decision,kind", GENUINE_KEPT)
    def test_an_agreed_hiring_process_is_booked(self, decision, kind):
        assert agent.calendar_invite_verdict(relevance(decision, kind)) == "BOOK"

    @pytest.mark.parametrize("decision,kind", GENUINE_CONTRADICTORY)
    def test_a_contradictory_answer_goes_to_review(self, decision, kind):
        assert agent.calendar_invite_verdict(relevance(decision, kind)) == "REVIEW"

    @pytest.mark.parametrize("decision,kind", MARKETING)
    def test_not_established_is_still_ignored(self, decision, kind):
        """The whole webinar defence, unchanged."""
        assert agent.calendar_invite_verdict(relevance(decision, kind)) == "IGNORE"

    def test_a_missing_or_junk_answer_is_ignored_not_reviewed(self):
        for value in ({}, {"decision": None}, {"decision": "nonsense"},
                      {"message_kind": "RECIPIENT_HIRING_PROCESS"}):
            assert agent.calendar_invite_verdict(value) == "IGNORE"

    def test_review_is_never_also_bookable(self):
        """The booking predicate must stay strict; only the drop is relaxed."""
        for decision, kind in GENUINE_CONTRADICTORY:
            value = relevance(decision, kind)
            assert agent.calendar_invite_needs_review(value) is True
            assert agent.calendar_invite_is_a_candidate_interview(value) is False


class TestWhatProcessMessageDoesWithEachVerdict:
    @staticmethod
    def _source():
        return inspect.getsource(agent.process_message)

    def test_review_does_not_mark_the_mail_ignored(self):
        source = self._source()
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        assert "IGNORED_NOT_OFFER_RELATED" not in block
        assert "archive_event_for_message" not in block
        assert "return None" not in block

    def test_review_produces_a_record_an_operator_sees(self):
        source = self._source()
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        assert 'status="MANUAL_REVIEW_REQUIRED"' in block
        assert 'classification="needs_review"' in block
        assert "should_create_review_record=True" in block
        assert "requires_manual_review=True" in block

    def test_review_is_not_a_booking(self):
        """It keeps the .ics schedule, but never claims the invite is confirmed."""
        source = self._source()
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        assert "needs-review" in block
        assert 'model, duration = calendar_result, "rfc5545-authenticated", 0' not in block

    def test_the_review_answer_is_kept_for_audit(self):
        source = self._source()
        block = source[source.index('if verdict == "REVIEW"'):source.index('elif verdict == "IGNORE"')]
        assert 'calendar_result["recruitment_relevance_result"]' in block

    def test_ignore_still_archives_and_returns(self):
        source = self._source()
        block = source[source.index('elif verdict == "IGNORE"'):source.index('else:\n            calendar_result = dict')]
        assert "CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW" in block
        assert "IGNORED_NOT_OFFER_RELATED" in block
        assert "return None" in block

    def test_book_is_the_only_path_that_authenticates_the_invite(self):
        source = self._source()
        assert source.count('"rfc5545-authenticated", 0') == 1

    def test_each_verdict_has_exactly_one_branch(self):
        source = self._source()
        assert source.count('if verdict == "REVIEW"') == 1
        assert source.count('elif verdict == "IGNORE"') == 1

    def test_the_intent_check_still_precedes_trusting_the_invite(self):
        source = self._source()
        assert source.index("calendar_invite_intent(") < source.index('"rfc5545-authenticated", 0')

    def test_a_gateway_failure_still_parks_rather_than_deciding(self):
        source = self._source()
        assert "CALENDAR_INTENT_UNAVAILABLE" in source
        failure = source.index("CALENDAR_INTENT_UNAVAILABLE")
        assert "AI_RETRY_PENDING" in source[failure - 400:failure + 400]


class TestTheGateStillAsksTheModel:
    def test_no_deterministic_shortcut_came_back(self):
        source = inspect.getsource(agent.calendar_invite_intent)
        assert "RELEVANCE_PROMPT" in source
        assert "_deterministic_relevance_result" not in source

    def test_the_verdict_reads_only_the_model_s_two_fields(self):
        """No keyword rule may creep in here: the model's answer decides."""
        source = inspect.getsource(agent.calendar_invite_verdict)
        code = source[source.index('"""', source.index('"""') + 3) + 3:]
        for forbidden in ("subject", "body", "sender", "re.search", "classify_context"):
            assert forbidden not in code
        assert 'relevance.get("decision")' in code
        assert 'relevance.get("message_kind")' in code
