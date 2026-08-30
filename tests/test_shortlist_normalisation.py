"""Normalising a model result that plainly describes a shortlist.

Fresh Ollama output for Gmail 19ff43450cdca797 read the mail correctly —
candidate_status "Interview Shortlisted", is_selection_or_offer_related true,
reason naming the provisional shortlist — but parked it at
MANUAL_REVIEW_REQUIRED/INTERVIEW_UPDATE because no interview slot was offered.
"""

from services.recruitment_mail_agent import normalise_shortlist_status

INFOSHARE_MESSAGE = {
    "subject": "Re: Your candidature has been provisionally shortlisted",
    "body": (
        "Hi Charan,\n\nAs discussed please share the documents..\n\n"
        "Your profile is provisionally shortlisted for Python Django with "
        "Infoshare systems.\n\nTo move on further HR Discussion need your "
        "documents. Please share below list of documents\n"
        "1. The offer, relieving and experience letters\n2. PAN Card\n"
        "3. AADHAR Card\n4. Academic certificates\n5. Pay slips and bank statements\n"
    ),
}


def _ollama_result(**overrides):
    """The fresh production result, verbatim in the fields that matter."""
    base = {
        "status": "MANUAL_REVIEW_REQUIRED",
        "classification": "interview_update",
        "candidate_status": "Interview Shortlisted",
        "lifecycle_event": "INTERVIEW_UPDATE",
        "confidence": 0.85,
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "requires_manual_review": True,
        "reason": (
            "The email does not provide any specific schedule details for the "
            "interview, only a request for documents to proceed with the "
            "selection process."
        ),
        "summary": (
            "The email confirms the candidate's profile is provisionally "
            "shortlisted for a Python Django position at Infoshare Systems Inc."
        ),
    }
    base.update(overrides)
    return base


def test_role_shortlist_without_interview_process_is_not_promoted():
    result = _ollama_result()
    assert normalise_shortlist_status(result, INFOSHARE_MESSAGE) is False
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"


def test_role_shortlist_wording_is_not_mislabeled_as_interview_shortlist():
    result = _ollama_result(status="INTERVIEW_UPDATE", candidate_status="Shortlisted")
    message = {"subject": "Update", "body": "You have been shortlisted for the role."}
    assert normalise_shortlist_status(result, message) is False
    assert result["status"] == "INTERVIEW_UPDATE"


def test_explicit_interview_shortlist_is_promoted():
    result = _ollama_result(
        status="INTERVIEW_UPDATE", candidate_status="Interview Shortlisted",
        evidence=[{
            "source": "EMAIL_BODY", "meaning": "INTERVIEW_SHORTLISTED",
            "text": "You have been shortlisted for the technical interview",
        }],
    )
    message = {
        "subject": "Interview update",
        "body": "You have been shortlisted for the technical interview. We will contact you with the schedule.",
    }
    assert normalise_shortlist_status(result, message) is True
    assert result["status"] == "INTERVIEW_SHORTLISTED"


def test_a_generic_document_request_is_not_promoted():
    """No shortlist wording anywhere, so the label alone must not be enough."""
    result = _ollama_result(candidate_status="Interview Shortlisted")
    message = {
        "subject": "Documents required",
        "body": "Please share your PAN card, AADHAR card and last 3 payslips.",
    }
    assert normalise_shortlist_status(result, message) is False
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"


def test_an_ambiguous_recruitment_update_stays_in_manual_review():
    result = _ollama_result(candidate_status="Profile Active")
    message = {"subject": "Your application", "body": "We are reviewing your profile."}
    assert normalise_shortlist_status(result, message) is False
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"


def test_reprocessing_the_same_result_is_idempotent():
    """A retry must reach the same status, never a second differing outcome."""
    message = {
        "subject": "Interview update",
        "body": "You have been shortlisted for the technical interview.",
    }
    evidence = [{
        "source": "EMAIL_BODY", "meaning": "INTERVIEW_SHORTLISTED",
        "text": "You have been shortlisted for the technical interview",
    }]
    first = _ollama_result(evidence=evidence)
    assert normalise_shortlist_status(first, message) is True
    # Re-running over an already-normalised result changes nothing further:
    # INTERVIEW_SHORTLISTED is not promotable, so it cannot be rewritten.
    assert normalise_shortlist_status(first, message) is False
    assert first["status"] == "INTERVIEW_SHORTLISTED"

    second = _ollama_result(evidence=evidence)
    normalise_shortlist_status(second, message)
    assert second["status"] == first["status"]


def test_a_stronger_outcome_is_never_overwritten():
    for stronger in ("INTERVIEW_CONFIRMED", "INTERVIEW_CANCELLED",
                     "OFFER_LETTER_RECEIVED", "CANDIDATE_REJECTED"):
        result = _ollama_result(status=stronger)
        assert normalise_shortlist_status(result, INFOSHARE_MESSAGE) is False
        assert result["status"] == stronger


def test_the_selection_flag_is_required():
    result = _ollama_result(is_selection_or_offer_related=False)
    assert normalise_shortlist_status(result, INFOSHARE_MESSAGE) is False
