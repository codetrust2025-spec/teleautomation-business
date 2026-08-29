"""An alert must not be headed with a status from a different classification.

The Innominds "Welcome aboard - please complete the pre-onboarding formalities"
was classified joining_confirmed and reached Mail Alerts under Selection
Related headed "Profile Active" - the label belonging to not_relevant. The
model returns `status` and `candidate_status` as separate fields and they
contradicted each other; finalize_detection trusted the label over the
classification everything else keys on.

The digiverifier BGV mail, same classification, same run, read "Joining
Confirmed". So an operator scanning the queue saw two identical outcomes
labelled as if one of them were nothing at all.
"""

from __future__ import annotations

import pytest

from core.recruitment_mail_store import (
    _CLASSIFICATION_STATUS, _agreeing_candidate_status,
)


class TestALabelFromAnotherClassificationIsReplaced:
    def test_the_innominds_case(self):
        result = {"candidate_status": "Profile Active"}
        assert _agreeing_candidate_status(result, "joining_confirmed") == "Joining Confirmed"

    @pytest.mark.parametrize("classification,wrong_label,expected", [
        ("joining_confirmed", "Profile Active", "Joining Confirmed"),
        ("offer_received", "Rejected", "Offer Received"),
        ("candidate_rejected", "Selected", "Rejected"),
        ("interview_confirmed", "Needs Review", "Interview Confirmed"),
        ("job_selection_confirmed", "Profile Active", "Selected"),
    ])
    def test_contradictions_resolve_to_the_classification(self, classification, wrong_label, expected):
        assert _agreeing_candidate_status(
            {"candidate_status": wrong_label}, classification) == expected

    def test_an_empty_label_falls_back_to_the_classification(self):
        assert _agreeing_candidate_status({}, "offer_accepted") == "Offer Accepted"


class TestAgreeingLabelsAreLeftAlone:
    def test_a_matching_label_is_kept(self):
        assert _agreeing_candidate_status(
            {"candidate_status": "Joining Confirmed"}, "joining_confirmed") == "Joining Confirmed"

    @pytest.mark.parametrize("classification", sorted(_CLASSIFICATION_STATUS))
    def test_every_classification_accepts_its_own_label(self, classification):
        label = _CLASSIFICATION_STATUS[classification]
        assert _agreeing_candidate_status({"candidate_status": label}, classification) == label

    def test_shared_labels_are_not_fought_over(self):
        """Four classifications map to "Joining Confirmed"; each may use it."""
        for classification in ("joining_confirmed", "joining_date_updated",
                               "onboarding_started", "background_verification"):
            assert _agreeing_candidate_status(
                {"candidate_status": "Joining Confirmed"}, classification) == "Joining Confirmed"

    def test_a_label_the_map_has_never_heard_of_is_preserved(self):
        """The model may be more specific than the map; only demonstrable
        contradictions are overridden."""
        assert _agreeing_candidate_status(
            {"candidate_status": "Joined"}, "joining_confirmed") == "Joined"
