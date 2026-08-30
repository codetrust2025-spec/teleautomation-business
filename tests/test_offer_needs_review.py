"""A distrusted offer must stay auditable without becoming a false offer.

Production message 4dbf2094 ("Intent Offer Letter - Infoshare"): Ollama returned
an offer/joining outcome at 95% with verbatim evidence, and
validate_lifecycle_event collapsed it to NONE / JOINING_CONFIRMATION_NOT_ASSERTED
so no event, no audit row, no notification. The backend may withhold every side
effect, but it may not delete the record.
"""

from core.recruitment_offer_visibility import ALLOWED_STATUSES, should_show_in_selection_offer_review
from services import recruitment_mail_agent as agent


class TestDowngradeMapping:
    def test_offer_family_maps_to_offer_review(self):
        for s in ("OFFER_IN_PROGRESS", "OFFER_LETTER_RECEIVED", "OFFER_INDICATION",
                  "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED"):
            assert agent._needs_review_status(s) == "OFFER_NEEDS_REVIEW", s

    def test_joining_family_maps_to_joining_review(self):
        for s in ("JOINING_CONFIRMED", "JOINED", "POST_SELECTION_ONBOARDING",
                  "JOINING_DATE_UPDATED"):
            assert agent._needs_review_status(s) == "JOINING_NEEDS_REVIEW", s

    def test_other_families_route_to_their_own_review_state(self):
        """Widened by the no-silent-delete audit: every tracked status lands
        somewhere visible, but each family keeps its own review state."""
        assert agent._needs_review_status("INTERVIEW_CONFIRMED") == "INTERVIEW_PROPOSED"
        assert agent._needs_review_status("CANDIDATE_REJECTED") == "SELECTION_NEEDS_REVIEW"

    def test_an_unknown_status_is_still_not_invented(self):
        assert agent._needs_review_status("NOT_A_REAL_STATUS") is None
        assert agent._needs_review_status("") is None


class TestNoSideEffects:
    """A review status must never feed booking/offer-case/acceptance flows."""

    def test_review_statuses_are_not_offer_cases(self):
        assert "OFFER_NEEDS_REVIEW" not in agent.OFFER_CASE_STATUSES
        assert "JOINING_NEEDS_REVIEW" not in agent.OFFER_CASE_STATUSES

    @staticmethod
    def _unsupported_offer():
        from tests.test_recruitment_pipeline import structured
        return structured("OFFER_LETTER_RECEIVED", .95, "Live session at 7 PM")

    def test_unsupported_offer_is_not_a_review_record(self):
        row = self._unsupported_offer()
        agent.validate_result(row, {"subject": "Public session", "body": "Live session at 7 PM"})
        assert row["status"] == "IGNORED_NOT_OFFER_RELATED"
        assert row["should_create_review_record"] is False
        assert row["backend_transition_validated"] is False

    def test_unsupported_offer_keeps_model_confidence_for_audit(self):
        row = self._unsupported_offer()
        agent.validate_result(row, {"subject": "Public session", "body": "Live session at 7 PM"})
        assert row["confidence"] == .95

    def test_candidate_facing_label_does_not_read_as_a_real_offer(self):
        row = self._unsupported_offer()
        agent.validate_result(row, {"subject": "Public session", "body": "Live session at 7 PM"})
        assert row["candidate_status"] == "Profile Active"


class TestVisibility:
    def _event(self, status, *, confidence=0.95):
        return {"primary_status": status, "review_status": "PENDING",
                "confidence": confidence, "validation_status": "NEEDS_REVIEW",
                "structured_result": {"is_selection_or_offer_related": True, "interview": {},
                    "evidence": [{"source": "EMAIL_BODY", "meaning": "OFFER_NEEDS_REVIEW",
                        "text": "We are delighted to offer you the position of Senior Software Engineer"}]}}

    def test_offer_review_is_visible(self):
        assert should_show_in_selection_offer_review(self._event("OFFER_NEEDS_REVIEW")) is True

    def test_joining_review_is_visible(self):
        assert should_show_in_selection_offer_review(self._event("JOINING_NEEDS_REVIEW")) is True

    def test_both_are_allowed_statuses(self):
        assert "OFFER_NEEDS_REVIEW" in ALLOWED_STATUSES
        assert "JOINING_NEEDS_REVIEW" in ALLOWED_STATUSES

    def test_low_confidence_still_hidden(self):
        assert should_show_in_selection_offer_review(
            self._event("OFFER_NEEDS_REVIEW", confidence=0.4)) is False

    def test_no_evidence_still_hidden(self):
        ev = self._event("OFFER_NEEDS_REVIEW")
        ev["structured_result"]["evidence"] = []
        assert should_show_in_selection_offer_review(ev) is False

    def test_ignored_statuses_stay_hidden(self):
        assert should_show_in_selection_offer_review(self._event("IGNORED_NOT_OFFER_RELATED")) is False


class TestFalseOfferProtectionIntact:
    def test_a_disclaimer_is_not_offer_evidence(self):
        """The guard that started this: a disclaimer must not read as an offer."""
        body = ("This interview invitation shall not be assumed to be an employment offer "
                "unless there is a formal offer issued by the company.")
        statuses = {s for s, _p in agent._matching_statuses(body)}
        assert "OFFER_LETTER_RECEIVED" not in statuses
        assert "OFFER_INDICATION" not in statuses

    def test_a_document_checklist_is_not_an_appointment_letter(self):
        body = ("Please upload the required documents: offer letter and relieving letter "
                "from your previous companies. This is a mandatory checklist.")
        statuses = {s for s, _p in agent._matching_statuses(body)}
        assert "APPOINTMENT_LETTER_RECEIVED" not in statuses

    def test_a_real_offer_still_matches(self):
        statuses = {s for s, _p in agent._matching_statuses(
            "We are delighted to offer you the position of Senior Software Engineer.")}
        assert statuses, "a genuine offer must still produce offer signals"
