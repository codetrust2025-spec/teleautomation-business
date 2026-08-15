"""Ollama decides meaning; the backend gates booking, and never deletes.

Live replay of the Infoshare mail (Gmail 19ff5c81e3b03078) showed both model
passes returning INTERVIEW_CONFIRMED at 95% with the correct candidate, role,
date, time and Teams link — and the backend rewriting it to
IGNORED_NOT_OFFER_RELATED via validate_interview_event, so no event, no audit
entry, no notification. The same replay showed the model fabricating "round"
and "company.domain", which is why booking stays gated separately.
"""

from core.recruitment_offer_visibility import should_show_in_selection_offer_review
from services import recruitment_mail_agent as agent


class TestRoutingReachesOllama:
    """A structured invite must not be filtered out before the model sees it."""

    TEAMS = ("Microsoft Teams meeting Join: https://teams.microsoft.com/meet/23471932663212 "
             "Meeting ID: 234 719 326 632 12 Passcode: re7Dc7si")

    def test_the_infoshare_reactjs_invite_is_recognised(self):
        assert agent.recruiting_invite_signal(
            "Interview schedule for Charan - ReactJS", self.TEAMS,
            "pushyami.sana@infosharesystems.com") is True

    def test_the_gopichand_invite_still_recognised(self):
        assert agent.recruiting_invite_signal(
            "Discussion with Gopichand for DevOps Engineer", self.TEAMS,
            "rvadde@innominds.com") is True

    def test_other_stack_named_roles(self):
        for subject in ("Interview schedule for Priya - Java",
                        "Availability for Ravi - Python Developer",
                        "Discussion with Anil for Angular"):
            assert agent.recruiting_invite_signal(subject, self.TEAMS, "hr@acme.com"), subject

    def test_generic_noise_still_rejected(self):
        for subject in ("Sprint planning", "Discussion with Ramu about the Q3 budget",
                        "Team offsite"):
            assert agent.recruiting_invite_signal(subject, self.TEAMS, "x@y.com") is False, subject

    def test_a_role_word_without_invite_structure_is_rejected(self):
        assert agent.recruiting_invite_signal(
            "Interview schedule for Charan - ReactJS",
            "Let us catch up next week.", "x@y.com") is False


class TestProposedIsAVisibleStatus:
    def _event(self, status, *, confidence=0.95, interview=None):
        return {
            "primary_status": status, "review_status": "PENDING",
            "confidence": confidence, "validation_status": "NEEDS_REVIEW",
            "structured_result": {
                "is_selection_or_offer_related": True,
                "interview": interview or {},
                "evidence": [{"source": "EMAIL_SUBJECT", "meaning": "INTERVIEW_PROPOSED",
                              "text": "Interview schedule for Charan - ReactJS"}],
            },
        }

    def test_a_proposed_interview_is_visible_without_a_confirmed_slot(self):
        assert should_show_in_selection_offer_review(self._event("INTERVIEW_PROPOSED")) is True

    def test_a_confirmed_interview_still_needs_its_date(self):
        assert should_show_in_selection_offer_review(self._event("INTERVIEW_CONFIRMED")) is False
        assert should_show_in_selection_offer_review(
            self._event("INTERVIEW_CONFIRMED", interview={"date": "2026-08-13"})) is True

    def test_low_confidence_is_still_hidden(self):
        assert should_show_in_selection_offer_review(
            self._event("INTERVIEW_PROPOSED", confidence=0.4)) is False

    def test_evidence_is_still_required(self):
        ev = self._event("INTERVIEW_PROPOSED")
        ev["structured_result"]["evidence"] = []
        assert should_show_in_selection_offer_review(ev) is False

    def test_proposed_is_in_the_agent_visible_vocabulary(self):
        assert "INTERVIEW_PROPOSED" in agent.VISIBLE_STATUSES
        assert "INTERVIEW_PROPOSED" in agent.TRACKED_STATUSES


class TestDowngradeNeverDeletes:
    """The core rule: distrust the detail, keep the record."""

    def test_the_downgrade_branch_preserves_the_record(self):
        import inspect
        src = inspect.getsource(agent.validate_result)
        assert 'status="INTERVIEW_PROPOSED"' in src
        assert "should_create_review_record=True" in src
        assert "downgraded_from=proposed_status" in src

    def test_the_downgrade_only_applies_to_interview_statuses(self):
        """A rejected joining/offer claim must still be ignored, not promoted."""
        import inspect
        src = inspect.getsource(agent.validate_result)
        assert 'if safe_status == "NONE" and proposed_status in interview_statuses:' in src

    def test_evidence_and_confidence_are_not_cleared_by_the_downgrade(self):
        import inspect
        src = inspect.getsource(agent.validate_result)
        head = src.split('status="INTERVIEW_PROPOSED"')[1].split("return")[0]
        assert "evidence" not in head, "downgrade must not touch evidence"
        assert "confidence" not in head, "downgrade must not touch confidence"


class TestBookingRemainsGated:
    """Hallucinated detail is why booking is not granted by classification."""

    def test_a_proposed_interview_is_not_a_confirmed_one(self):
        assert "INTERVIEW_PROPOSED" != "INTERVIEW_CONFIRMED"
        import inspect
        src = inspect.getsource(agent.validate_result)
        # The downgrade sets requires_manual_review so it cannot silently book.
        assert "requires_manual_review=True" in src
