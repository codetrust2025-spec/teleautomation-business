"""A review request the model actually made must reach the review queue.

An Accenture "Your Interview has been successfully Scheduled" was classified by
Ollama, found to have a time nobody could parse, and downgraded to
MANUAL_REVIEW_REQUIRED - which is the correct outcome. It was then hidden by
should_show_in_selection_offer_review, whose vocabulary check compares the
model's evidence `meaning` fields against a fixed set. The row was written with
review_status IGNORED and visible_in_offer_review False: it existed, and no
screen in the product would show it.

That check exists for a different case - an AI timeout produces a
MANUAL_REVIEW_REQUIRED row carrying no judgement at all, and those must stay
hidden while the recovery worker retries them. The distinction is whether a
model ran, not what wording it chose.
"""

from __future__ import annotations

import pytest

from core.recruitment_offer_visibility import should_show_in_selection_offer_review


def review_row(*, source, validation="NEEDS_REVIEW", meanings=("some prose",),
               confidence=1.0, status="MANUAL_REVIEW_REQUIRED"):
    return {
        "primary_status": status,
        "review_status": "PENDING",
        "confidence": confidence,
        "validation_status": validation,
        "structured_result": {
            "classification_source": source,
            "validation_status": validation,
            "evidence": [{"source": "EMAIL_BODY", "meaning": m, "text": m} for m in meanings],
        },
    }


class TestOllamaReviewRequestsAreVisible:
    def test_the_accenture_case(self):
        """Model ran, asked for review, wrote its evidence meaning as prose."""
        row = review_row(source="OLLAMA",
                         meanings=("Interview schedule could not be read",))
        assert should_show_in_selection_offer_review(row) is True

    @pytest.mark.parametrize("prose", [
        "Request to complete pre-onboarding formalities",
        "Invitation - Digital Employment BGV_18310757",
        "They decided not to move forward with the candidature",
    ])
    def test_prose_meanings_do_not_hide_the_row(self, prose):
        assert should_show_in_selection_offer_review(
            review_row(source="OLLAMA", meanings=(prose,))) is True

    def test_no_evidence_meanings_at_all_still_shows(self):
        assert should_show_in_selection_offer_review(
            review_row(source="OLLAMA", meanings=())) is False, (
            "evidence is still required; only the vocabulary check was scoped"
        )


class TestInfrastructureFailuresStayHidden:
    def test_a_timeout_row_is_still_hidden(self):
        """No model ran, so there is no judgement to surface - and the recovery
        worker is still retrying it."""
        row = review_row(source="FAILURE_REVIEW", validation="RETRY_PENDING")
        assert should_show_in_selection_offer_review(row) is False

    def test_an_outage_fallback_row_is_still_hidden(self):
        row = review_row(source="FALLBACK")
        assert should_show_in_selection_offer_review(row) is False

    def test_a_retry_pending_ollama_row_is_still_hidden(self):
        """Ollama is named but the row is mid-retry, so it carries no verdict."""
        row = review_row(source="OLLAMA", validation="RETRY_PENDING")
        assert should_show_in_selection_offer_review(row) is False

    def test_a_low_confidence_row_is_still_hidden(self):
        row = review_row(source="OLLAMA", confidence=0.4)
        assert should_show_in_selection_offer_review(row) is False

    def test_a_fallback_row_with_a_strong_meaning_still_shows(self):
        """The original escape hatch is intact for rows no model produced."""
        row = review_row(source="FALLBACK", meanings=("OFFER_RECEIVED",))
        assert should_show_in_selection_offer_review(row) is True
