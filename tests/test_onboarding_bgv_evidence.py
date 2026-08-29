"""Onboarding and BGV prose must reach the alert, and adverts must not.

Both mails classified correctly and were then refused by
should_route_to_mail_alert, because the model wrote its evidence meaning as
prose - "Request to complete pre-onboarding formalities", "Invitation - Digital
Employment BGV_18310757" - and the gate compares meanings against a fixed
vocabulary. Correct classification, no alert, no trace.

The gate itself is not being loosened. It is the component actually doing the
precision work: measured over 76 real August mails with the gate bypassed, the
classifier called 74-88% of ordinary mail a lifecycle event, including a
personal-loan advert as offer_declined and a grocery promotion as
interview_shortlisted. So the second half of this file matters as much as the
first - the phrases have to be ones no advert uses.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services.recruitment_mail_agent import normalise_evidence_meaning


def meaning_of(text, meaning="some prose the model wrote"):
    return normalise_evidence_meaning(
        {"source": "EMAIL_BODY", "meaning": meaning, "text": text}
    )["meaning"]


class TestTheTwoMailsThatWereRefused:
    def test_innominds_pre_onboarding_reaches_a_recognised_meaning(self):
        promoted = normalise_evidence_meaning({
            "source": "EMAIL_BODY",
            "meaning": "Request to complete pre-onboarding formalities",
            "text": "we request you to complete the pre-onboarding formalities",
        })
        assert promoted["meaning"] == "POST_SELECTION_ONBOARDING"

    def test_digiverifier_bgv_reaches_a_recognised_meaning(self):
        promoted = normalise_evidence_meaning({
            "source": "EMAIL_SUBJECT",
            "meaning": "Invitation - Digital Employment BGV_18310757",
            "text": "Invitation - Digital Employment BGV_18310757",
        })
        assert promoted["meaning"] == "BACKGROUND_VERIFICATION"

    @pytest.mark.parametrize("classification,promoted", [
        ("joining_confirmed", "POST_SELECTION_ONBOARDING"),
        ("joining_confirmed", "BACKGROUND_VERIFICATION"),
    ])
    def test_the_promoted_meaning_satisfies_the_gate(self, classification, promoted):
        """What the gate actually compares against, so this cannot pass while
        the alert still gets refused."""
        expected = {k for k, v in store._STATUS_CLASSIFICATION.items()
                    if v == classification} | {classification.upper()}
        assert promoted in expected

    def test_the_original_prose_is_preserved_as_the_audit_trail(self):
        promoted = normalise_evidence_meaning({
            "source": "EMAIL_BODY",
            "meaning": "Request to complete pre-onboarding formalities",
            "text": "complete the pre-onboarding formalities",
        })
        assert promoted["meaning_text"] == "Request to complete pre-onboarding formalities"


class TestTheGateIsNotLoosened:
    @pytest.mark.parametrize("text", [
        # Real August false positives, from the rescan.
        "Jobrapido's latest job listings: browse and apply today",
        "We are hiring for AWS SRE Engineer. If interested kindly share your updated resume",
        "Your recent visit unlocked an exclusive offer. Activate 25% off",
        "1.69 Lacs Personal Loan - check EMI in just 2 steps",
        "Your ticket against PNR Number 4342416157 has been successfully cancelled",
        # The near-miss that must stay a miss: an advert describing a check,
        # not a check commissioned on a person.
        "Selected candidates will undergo background verification before joining",
        "Background verification is mandatory for this role",
        "Onboarding support and training provided to all new hires",
    ])
    def test_ordinary_mail_is_not_promoted(self, text):
        assert meaning_of(text) == "some prose the model wrote"

    def test_an_already_valid_meaning_is_left_alone(self):
        item = {"source": "EMAIL_BODY", "meaning": "OFFER_LETTER_RECEIVED",
                "text": "we are pleased to offer you"}
        assert normalise_evidence_meaning(item) is item

    def test_an_empty_meaning_is_left_alone(self):
        item = {"source": "EMAIL_BODY", "meaning": "", "text": "anything at all"}
        assert normalise_evidence_meaning(item) is item
