"""Two readers naming different stages is not a question for a person.

`_reconcile_model_results` reads every mail twice and, when the two statuses
were not the identical string, produced MANUAL_REVIEW_REQUIRED. That was 710 of
the 719 review outcomes ever recorded, and 433 of the 453 in the week before
this change -- about a fifth of everything the model produced, resolving to no
decision at all.

Measured over thirty days of production, all 489 disagreements were of one
shape: both readers agree the mail is a real recruitment event and name a
different stage.

    242  INTERVIEW_SHORTLISTED  /  SELECTED
     97  INTERVIEW_UPDATE       /  SELECTED
     76  INTERVIEW_UPDATE       /  INTERVIEW_SHORTLISTED
     28  SELECTED               /  INTERVIEW_SHORTLISTED
     20  INTERVIEW_CONFIRMED    /  INTERVIEW_SHORTLISTED

Not one was positive against negative. Requiring two independent readings of a
thirty-value enum to agree on the exact string is a much stricter test than the
decision needs, because only three classifications can move a booking:
confirmed, rescheduled and cancelled. When neither reading is one of those, the
two disagree about the label to show, not about what to do.

So the detection is unchanged and the outcome is not. A stage-only
disagreement resolves to the earlier stage and then has to earn its way through
every ordinary guard -- entailment, evidence, confidence, payment, duplicate,
conflict. A disagreement that touches a booking, or where one reader says
nothing happened at all, is genuinely undecided: it returns for an automatic
retry, which a different execution state often settles
(`ollama-answers-differ-by-execution-state`), and never reaches a person.

466 of the 489 resolve; the 23 that could move a booking retry.
"""

from __future__ import annotations

import inspect

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent


def side(status, *, confidence=0.9, evidence=None):
    return {
        "status": status, "confidence": confidence,
        "evidence": evidence if evidence is not None else [{"source": "EMAIL_BODY", "text": "quoted"}],
        "risk_flags": [],
    }


def reconcile(primary, validator):
    return agent._reconcile_model_results(side(primary), side(validator))


def resolution(primary, validator):
    return reconcile(primary, validator)["model_validation"].get("resolution")


#: Every distinct pair observed in production over thirty days, with its count.
PRODUCTION_PAIRS = [
    (242, "INTERVIEW_SHORTLISTED", "SELECTED"),
    (97, "INTERVIEW_UPDATE", "SELECTED"),
    (76, "INTERVIEW_UPDATE", "INTERVIEW_SHORTLISTED"),
    (28, "SELECTED", "INTERVIEW_SHORTLISTED"),
    (20, "INTERVIEW_CONFIRMED", "INTERVIEW_SHORTLISTED"),
    (10, "SELECTED", "INTERVIEW_UPDATE"),
    (2, "BACKGROUND_VERIFICATION", "SELECTED"),
    (2, "OFFER_IN_PROGRESS", "SELECTED"),
    (2, "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE"),
    (1, "BACKGROUND_VERIFICATION", "INTERVIEW_SHORTLISTED"),
    (1, "SELECTED", "OFFER_IN_PROGRESS"),
    (1, "DOCUMENT_VERIFICATION", "SELECTED"),
    (1, "INTERVIEW_CONFIRMED", "SELECTED"),
    (1, "INTERVIEW_RESCHEDULED", "INTERVIEW_CONFIRMED"),
    (1, "INTERVIEW_SHORTLISTED", "HR_CONFIRMATION"),
    (1, "INTERVIEW_SHORTLISTED", "INTERVIEW_CONFIRMED"),
    (1, "INTERVIEW_SHORTLISTED", "POST_SELECTION_ONBOARDING"),
    (1, "INTERVIEW_UPDATE", "CANDIDATE_REJECTED"),
    (1, "POST_SELECTION_ONBOARDING", "OFFER_IN_PROGRESS"),
]

BOOKING_RELEVANT = {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"}


class TestNobodyIsAskedAnything:
    @pytest.mark.parametrize("primary,validator", [(p, v) for _, p, v in PRODUCTION_PAIRS])
    def test_no_production_disagreement_produces_a_review(self, primary, validator):
        chosen = reconcile(primary, validator)
        assert chosen["status"] != "MANUAL_REVIEW_REQUIRED"
        assert chosen.get("requires_manual_review") is not True
        assert chosen.get("classification") != "needs_review"
        assert chosen.get("candidate_status") != "Needs Review"

    def test_the_whole_measured_population_splits_as_designed(self):
        """466 resolve, 23 retry -- the 23 being exactly the booking-relevant."""
        resolved = retried = 0
        for count, primary, validator in PRODUCTION_PAIRS:
            if resolution(primary, validator) == "CONSERVATIVE_STAGE":
                resolved += count
            else:
                retried += count
        assert (resolved, retried) == (466, 23)


class TestAStageOnlyDisagreementResolvesItself:
    @pytest.mark.parametrize("primary,validator,expected", [
        ("INTERVIEW_SHORTLISTED", "SELECTED", "INTERVIEW_SHORTLISTED"),
        ("INTERVIEW_UPDATE", "SELECTED", "INTERVIEW_UPDATE"),
        ("INTERVIEW_UPDATE", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE"),
        ("SELECTED", "INTERVIEW_SHORTLISTED", "INTERVIEW_SHORTLISTED"),
        ("SELECTED", "INTERVIEW_UPDATE", "INTERVIEW_UPDATE"),
    ])
    def test_the_earlier_stage_wins(self, primary, validator, expected):
        """Advancing a candidate on a contested reading is the irreversible half."""
        assert reconcile(primary, validator)["status"] == expected

    def test_the_choice_is_symmetric(self):
        """Which model happened to be primary must not decide the outcome.

        Symmetry is a property of the decision. On the retry path the retained
        payload is still the primary reader's, which is only what gets carried
        into the stored analysis -- the outcome there is a retry either way.
        """
        for _, primary, validator in PRODUCTION_PAIRS:
            forward, backward = reconcile(primary, validator), reconcile(validator, primary)
            assert (forward["model_validation"]["resolution"]
                    == backward["model_validation"]["resolution"]), (primary, validator)
            if forward["model_validation"]["resolution"] == "CONSERVATIVE_STAGE":
                assert forward["status"] == backward["status"], (primary, validator)

    def test_a_contested_reading_never_marks_someone_rejected(self):
        assert reconcile("INTERVIEW_UPDATE", "CANDIDATE_REJECTED")["status"] == "INTERVIEW_UPDATE"

    def test_the_adopted_reading_keeps_its_own_evidence(self):
        """Evidence must belong to the status actually taken, or the guard
        downstream is checking one reading's quote against another's claim."""
        primary = side("SELECTED", evidence=[{"source": "EMAIL_BODY", "text": "selected"}])
        validator = side("INTERVIEW_UPDATE", evidence=[{"source": "EMAIL_BODY", "text": "update"}])
        chosen = agent._reconcile_model_results(primary, validator)
        assert chosen["status"] == "INTERVIEW_UPDATE"
        assert chosen["evidence"] == [{"source": "EMAIL_BODY", "text": "update"}]

    def test_confidence_is_capped_below_the_auto_accept_threshold(self):
        chosen = reconcile("INTERVIEW_SHORTLISTED", "SELECTED")
        assert chosen["confidence"] <= 0.89

    def test_it_is_still_recorded_as_a_disagreement(self):
        """Resolving one is not the same as pretending it did not happen."""
        chosen = reconcile("INTERVIEW_SHORTLISTED", "SELECTED")
        assert chosen["model_validation"] == {
            "agreed": False,
            "primary_status": "INTERVIEW_SHORTLISTED",
            "validator_status": "SELECTED",
            "resolution": "CONSERVATIVE_STAGE",
            "resolved_status": "INTERVIEW_SHORTLISTED",
        }

    def test_the_reconciliation_is_not_recorded_as_a_risk_flag(self):
        """The tail of `validate_result` turns any risk flag into
        `requires_manual_review`, so a flag here would send every resolved
        disagreement back to the review state this change removes."""
        chosen = reconcile("INTERVIEW_SHORTLISTED", "SELECTED")
        assert not [f for f in chosen.get("risk_flags") or [] if "DISAGREEMENT" in str(f).upper()]


class TestABookingIsNeverDecidedByAResolvedDisagreement:
    @pytest.mark.parametrize("primary,validator", [
        ("INTERVIEW_CONFIRMED", "INTERVIEW_SHORTLISTED"),
        ("INTERVIEW_SHORTLISTED", "INTERVIEW_CONFIRMED"),
        ("INTERVIEW_CONFIRMED", "SELECTED"),
        ("INTERVIEW_RESCHEDULED", "INTERVIEW_CONFIRMED"),
        ("INTERVIEW_CANCELLED", "INTERVIEW_UPDATE"),
        ("INTERVIEW_CONFIRMED", "INTERVIEW_CANCELLED"),
    ])
    def test_anything_touching_a_booking_retries_instead(self, primary, validator):
        chosen = reconcile(primary, validator)
        assert chosen["model_validation"]["resolution"] == "AUTOMATIC_RETRY"
        assert chosen["requires_manual_review"] is False
        assert chosen["should_create_review_record"] is False
        # The conversion itself happens in `validate_result`, after the schema
        # check -- see TestTheSchemaIsTheModelsContract.
        assert "MODEL_DISAGREEMENT" in chosen["risk_flags"]

    def test_a_resolved_disagreement_can_never_be_an_actionable_class(self):
        """The safety property in one line: nothing this branch resolves to can
        reach `interview_auto_booking.ACTIONABLE`."""
        from services.interview_auto_booking import ACTIONABLE
        for _, primary, validator in PRODUCTION_PAIRS:
            chosen = reconcile(primary, validator)
            if chosen["model_validation"]["resolution"] != "CONSERVATIVE_STAGE":
                continue
            assert store.canonical_classification(chosen) not in ACTIONABLE, (primary, validator)


class TestUncertaintyIsNeverSilence:
    @pytest.mark.parametrize("primary,validator", [
        ("SELECTED", "IGNORED_NOT_OFFER_RELATED"),
        ("IGNORED_NOT_OFFER_RELATED", "INTERVIEW_SHORTLISTED"),
        ("INTERVIEW_CONFIRMED", "IGNORED_NOT_OFFER_RELATED"),
        ("INTERVIEW_UPDATE", "IGNORED_LOW_CONFIDENCE"),
    ])
    def test_one_reader_saying_nothing_happened_retries_rather_than_ignoring(
        self, primary, validator,
    ):
        """Taking the conservative side of "is this anything at all?" would
        quietly drop the mail, which is how a real interview goes missing."""
        chosen = reconcile(primary, validator)
        assert chosen["model_validation"]["resolution"] == "AUTOMATIC_RETRY"
        assert chosen["should_create_review_record"] is False
        assert "MODEL_DISAGREEMENT" in chosen["risk_flags"]

    def test_an_unrankable_status_retries(self):
        assert resolution("INTERVIEW_SHORTLISTED", "SOMETHING_NEW") == "AUTOMATIC_RETRY"


class TestAgreementIsUntouched:
    def test_two_identical_readings_still_agree(self):
        chosen = reconcile("INTERVIEW_CONFIRMED", "INTERVIEW_CONFIRMED")
        assert chosen["model_validation"]["agreed"] is True
        assert chosen["status"] == "INTERVIEW_CONFIRMED"
        assert "resolution" not in chosen["model_validation"]

    def test_agreement_still_takes_the_lower_confidence(self):
        chosen = agent._reconcile_model_results(
            side("SELECTED", confidence=0.95), side("SELECTED", confidence=0.6))
        assert chosen["confidence"] == pytest.approx(0.6)


class TestTheRankItReliesOn:
    def test_the_progression_is_the_one_the_candidate_store_uses(self):
        assert store.stage_rank("INTERVIEW_UPDATE") < store.stage_rank("INTERVIEW_SHORTLISTED")
        assert store.stage_rank("INTERVIEW_SHORTLISTED") < store.stage_rank("SELECTED")

    @pytest.mark.parametrize("status", [
        "MANUAL_REVIEW_REQUIRED", "AI_RETRY_PENDING", "NOT_A_STATUS", "", None,
    ])
    def test_a_status_with_no_place_in_the_progression_is_unrankable(self, status):
        assert store.stage_rank(status) is None


class TestTheMailIsNotLostOnTheWayOut:
    def test_a_retry_result_leaves_by_the_retry_exit_not_the_ignore_branch(self):
        """`AI_RETRY_PENDING` is not a tracked status, so without its own exit
        the ignore branch marks it not-relevant and the mail disappears."""
        source = inspect.getsource(agent.process_message)
        retry_exit = source.index("_queued_for_another_attempt")
        ignore_branch = source.index('if not result.get("is_selection_or_offer_related")')
        assert retry_exit < ignore_branch

    def test_both_undecided_paths_share_that_exit(self):
        """One check before the ignore branch, one after the evidence guard."""
        source = inspect.getsource(agent.process_message)
        assert source.count("if _queued_for_another_attempt():") == 2

    def test_the_evidence_guard_retries_rather_than_asking_a_person(self):
        source = inspect.getsource(agent.process_message)
        guard = source.index("lacks source-supported evidence")
        block = source[guard - 700:guard + 200]
        assert "AI_RETRY_PENDING" in block
        assert "MANUAL_REVIEW_REQUIRED" not in block.split("primary_status=")[-1]

    def test_the_validator_no_longer_forces_a_review_on_disagreement(self):
        source = inspect.getsource(agent.validate_result)
        block = source[source.index('if "MODEL_DISAGREEMENT" in'):]
        block = block[:block.index("return") + 6]
        assert "AI_RETRY_PENDING" in block
        assert "MANUAL_REVIEW_REQUIRED" not in block
        assert "requires_manual_review=False" in block


class TestTheSchemaIsTheModelsContract:
    """`validate_result` schema-validates its input before anything else.

    Writing `AI_RETRY_PENDING` into `status` at reconcile time raised
    "invalid selection/offer JSON: 'AI_RETRY_PENDING' is not one of [...]" on
    every booking-relevant disagreement. That surfaced as a schema-validation
    failure -- naming the model as the cause when the pipeline had written the
    value itself -- and each occurrence spent one of the retry attempts that
    lead to a terminal `VALIDATION_FAILED` park.

    So the reconciler leaves `status` as the model wrote it and carries the
    decision in the risk flag, which the validator converts after the schema
    has passed.
    """

    @pytest.mark.parametrize("primary,validator", [(p, v) for _, p, v in PRODUCTION_PAIRS])
    def test_every_reconciled_result_still_satisfies_the_model_schema(self, primary, validator):
        from jsonschema import Draft202012Validator

        from services.recruitment_mail_agent import SCHEMA

        status = reconcile(primary, validator)["status"]
        enum = SCHEMA["properties"]["status"]["enum"]
        assert status in enum, f"{status} is not a value the schema allows"

    def test_the_retry_status_is_deliberately_not_in_the_model_schema(self):
        """If it ever is, the model itself could claim it."""
        from services.recruitment_mail_agent import SCHEMA

        assert "AI_RETRY_PENDING" not in SCHEMA["properties"]["status"]["enum"]

    def test_the_reconciler_does_not_write_the_retry_status(self):
        source = inspect.getsource(agent._reconcile_model_results)
        assert 'chosen["status"] = "AI_RETRY_PENDING"' not in source
