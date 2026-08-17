"""Shortlisting is a recruitment outcome, not only an interview preamble.

The reported production case: an agency wrote "Your profile is provisionally
shortlisted for Python Django with Infoshare systems" and then listed the
documents needed for the HR discussion. The shortlist signals required the word
"interview" straight after "shortlisted for", so nothing matched, the message
fell through to the model, and it was ignored for carrying no interview date or
time. The selection was the event; the document list was the next action.
"""

from services import recruitment_mail_agent as agent


def _statuses(text: str) -> set[str]:
    return {status for status, _phrase in agent._matching_statuses(text)}


INFOSHARE_BODY = (
    "Hi Charan,\n\nAs discussed please share the documents..\n\n"
    "Your profile is provisionally shortlisted for Python Django with "
    "Infoshare systems.\n\n"
    "To move on further HR Discussion need your documents. "
    "Please share below list of documents\n"
    "1. The offer, relieving and experience letters from your ALL-previous "
    "employments. ( IF Applicable)\n"
    "2. Photocopy of your PAN Card\n"
    "3. Photocopy of your AADHAR Card\n"
    "4. Photocopies of all your academic certificates\n"
    "5. Latest 3months Pay slips and 6 Months Bank statements ( Mandatory )\n"
    "6. Proof of the current address and Passport size photo.\n"
)


class TestSelectionIsDetected:
    def test_the_exact_production_message_is_shortlisted(self):
        assert "INTERVIEW_SHORTLISTED" in _statuses(INFOSHARE_BODY)

    def test_document_requests_after_selection_stay_a_shortlist(self):
        """The document list must not demote the selection that precedes it."""
        statuses = _statuses(INFOSHARE_BODY)
        assert "INTERVIEW_SHORTLISTED" in statuses
        # It must not be captured as an appointment/offer letter just because
        # the mail names offer and relieving letters among the documents.
        assert "OFFER_LETTER_RECEIVED" not in statuses
        assert "APPOINTMENT_LETTER_RECEIVED" not in statuses

    def test_provisionally_shortlisted(self):
        assert "INTERVIEW_SHORTLISTED" in _statuses(
            "Your candidature has been provisionally shortlisted for the Django role."
        )

    def test_shortlisted(self):
        for wording in (
            "You have been shortlisted for the position of Backend Engineer.",
            "Your profile has been shortlisted for the role.",
            "We have shortlisted your profile for further discussion.",
            "You are shortlisted for HR discussion.",
        ):
            assert "INTERVIEW_SHORTLISTED" in _statuses(wording), wording

    def test_selected(self):
        assert _statuses("We are pleased to inform you that you have been selected.")

    def test_moved_forward_to_the_next_stage(self):
        assert "INTERVIEW_SHORTLISTED" in _statuses(
            "Congratulations, you have been moved forward to the next stage."
        )


class TestGenericDocumentRequestsAreNotSelection:
    def test_a_bare_document_request_is_not_a_shortlist(self):
        body = (
            "Hi Charan,\n\nAs discussed please share the documents..\n\n"
            "Please share below list of documents\n"
            "1. Photocopy of your PAN Card\n"
            "2. Photocopy of your AADHAR Card\n"
            "3. Latest 3months Pay slips and 6 Months Bank statements\n"
        )
        assert "INTERVIEW_SHORTLISTED" not in _statuses(body)

    def test_an_onboarding_document_checklist_is_not_a_shortlist(self):
        assert "INTERVIEW_SHORTLISTED" not in _statuses(
            "Please upload the required documents to complete your profile "
            "verification. This is a mandatory checklist."
        )

    def test_the_word_shortlist_alone_is_not_enough(self):
        assert "INTERVIEW_SHORTLISTED" not in _statuses(
            "We maintain a shortlist of vendors for this engagement."
        )


# The audit engine used to carry a second copy of these shortlist phrases, and
# a class here asserted the two never disagreed. Mail Audit was decommissioned,
# so the notification path below is now the only classifier and there is no
# second opinion left to reconcile against.


class TestInterviewBehaviourIsPreserved:
    def test_interview_routing_still_detected(self):
        """Interview mail keeps routing as before — only the model may upgrade
        an update to confirmed/rescheduled/cancelled, so INTERVIEW_UPDATE is
        the status this deterministic layer is expected to produce."""
        assert "INTERVIEW_UPDATE" in _statuses(
            "Your interview scheduled for 13 Aug 2026 at 3 PM is confirmed."
        )

    def test_interview_cancellation_still_routes(self):
        assert "INTERVIEW_UPDATE" in _statuses(
            "Your interview cancelled — we will revert with a new slot."
        )

    def test_shortlisted_for_the_next_interview_still_detected(self):
        assert "INTERVIEW_SHORTLISTED" in _statuses(
            "You are shortlisted for the next interview round."
        )
