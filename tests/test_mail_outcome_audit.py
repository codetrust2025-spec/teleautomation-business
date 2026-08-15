"""Contract tests for the candidate mail outcome audit.

Every test runs offline against in-memory fixtures. None of them opens a Gmail
connection or a real database, so the suite can never read or modify a real
candidate mailbox.

The numbered scenarios mirror the audit specification: each one is a way an
outcome report can be wrong in a way that matters to a candidate.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_mail_audit as engine  # noqa: E402


UTC = timezone.utc


def message(**overrides):
    base = {
        "subject": "Update",
        "body": "",
        "sender_email": "recruiter@acme-corp.example",
        "sender_name": "Acme Talent",
        "authentication_results": "mx.google.com; spf=pass; dkim=pass; dmarc=pass",
        "received_spf": "pass",
        "message_direction": "INBOUND",
    }
    base.update(overrides)
    return base


def attachment(**overrides):
    base = {
        "filename": "document.pdf",
        "mime_type": "application/pdf",
        "attachment_type": "OTHER",
        "extraction_status": "COMPLETED",
        "checksum": "checksum-1",
        "text": "",
    }
    base.update(overrides)
    return base


# ── 1. Genuine interview invite ──────────────────────────────────────────────

def test_genuine_interview_invite_is_an_invite_not_a_selection():
    result = engine.classify_message(
        message(
            subject="Interview invitation - Backend Engineer",
            body="Your technical interview has been scheduled on 12 Aug 2026 at 10:30 AM IST. "
                 "Please join the Microsoft Teams meeting.",
        ),
        [attachment(filename="invite.ics", mime_type="text/calendar", text="BEGIN:VCALENDAR")],
    )
    assert result["outcome"] == engine.INTERVIEW_INVITE
    assert result["confidence"] >= 80
    assert result["manual_review_required"] is False
    assert engine.OUTCOME_RANK[result["outcome"]] < engine.OUTCOME_RANK[engine.FINAL_SELECTION]


def test_interview_without_date_or_time_needs_review():
    result = engine.classify_message(
        message(subject="Interview details", body="We would like to invite you for an interview soon."),
    )
    assert result["outcome"] == engine.INTERVIEW_INVITE
    assert result["manual_review_required"] is True


# ── 2. Next-round email ──────────────────────────────────────────────────────

def test_next_round_is_not_an_offer():
    result = engine.classify_message(
        message(
            subject="Congratulations - you have cleared the technical round",
            body="You have cleared the first round and are shortlisted for the next interview "
                 "with our engineering manager.",
        ),
    )
    assert result["outcome"] == engine.NEXT_ROUND
    assert result["outcome"] not in {engine.OFFER_INDICATION, engine.VERIFIED_OFFER_LETTER}
    assert "not an offer" in result["rationale"].lower()


# ── 3. Final selection ───────────────────────────────────────────────────────

def test_final_selection_is_recognised():
    result = engine.classify_message(
        message(
            subject="Selection confirmation",
            body="We are pleased to inform you that you have been selected for the position of "
                 "Senior Data Engineer.",
        ),
    )
    assert result["outcome"] == engine.FINAL_SELECTION
    assert result["confidence"] >= 80


def test_selection_wording_inside_an_interview_invite_is_not_final_selection():
    result = engine.classify_message(
        message(
            subject="Interview scheduled",
            body="You are selected for the technical round. Your interview has been scheduled "
                 "on 3 Sep 2026 at 4:00 PM.",
        ),
    )
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED
    assert result["manual_review_required"] is True


# ── 4. Real offer-letter PDF ─────────────────────────────────────────────────

OFFER_PDF_TEXT = (
    "OFFER OF EMPLOYMENT\n"
    "Dear Candidate,\n"
    "We are pleased to offer you the position of Senior Data Engineer at Acme Corp.\n"
    "Your annual CTC is INR 24,00,000 per annum.\n"
    "Your date of joining is 01 Sep 2026.\n"
)


def test_offer_letter_pdf_with_real_details_is_verified():
    result = engine.classify_message(
        message(subject="Your offer letter", body="Please find your offer letter attached."),
        [attachment(filename="Offer_Letter_Acme.pdf", attachment_type="OFFER_LETTER",
                    text=OFFER_PDF_TEXT)],
    )
    assert result["outcome"] == engine.VERIFIED_OFFER_LETTER
    assert result["confidence"] >= 90
    assert any(item["source"] == "ATTACHMENT" for item in result["evidence"])


def test_offer_letter_without_verifiable_details_is_not_verified():
    result = engine.classify_message(
        message(subject="Your offer letter", body="Please find your offer letter attached."),
        [attachment(filename="Offer_Letter.pdf", attachment_type="OFFER_LETTER",
                    text="Thank you for your time. Regards, HR.")],
    )
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED
    assert result["manual_review_required"] is True


def test_offer_intent_without_a_letter_is_only_an_indication():
    result = engine.classify_message(
        message(subject="Good news", body="We are pleased to offer you a role on our team. "
                                          "The formal paperwork will follow."),
    )
    assert result["outcome"] == engine.OFFER_INDICATION


# ── False positives found in the first production audit ──────────────────────
#
# Each of these was reported as a VERIFIED_OFFER_LETTER against a real
# candidate. They are the reason an offer letter must be a document that reads
# like an offer letter, and why "offer letter" as a bare phrase is worthless.

def test_recruiter_screening_form_is_not_an_offer():
    """"Holding any offer letter = no" is the candidate answering a form."""
    result = engine.classify_message(
        message(
            subject="Re: AWS",
            body="Please share: current ctc= 5 LPA, expected ctc= 12 LPA, "
                 "holding any offer letter= no, notice period= 60 days (negotiable), "
                 "highest qualification = B.Tech",
            sender_email="ranjith@realtekconsulting.example",
        ),
    )
    assert result["outcome"] == engine.NOT_RELEVANT
    assert "QUESTIONNAIRE" in result["signals"]


def test_job_advert_listing_document_requirements_is_not_an_offer():
    result = engine.classify_message(
        message(
            subject="Job | Urgent Requirements FOR DevSecOps Engineer FOR TCS - Hyderabad",
            body="Apply now. Candidates should have all the documents (offer letters and "
                 "relieving letters for all the previous organizations and offer letter "
                 "for the current organization). New jobs for you matching your profile.",
            sender_email="ngstaffing@naukri.com",
        ),
    )
    assert result["outcome"] == engine.NOT_RELEVANT


def test_screening_mail_asking_whether_an_offer_was_received_is_not_an_offer():
    result = engine.classify_message(
        message(
            subject="Video Interview - Share details ASAP",
            body="Date of birth: ; have you received TCS offer letter (TCS offer released) "
                 "before? current ctc: ; expected ctc: ; current location?",
            sender_email="nandhini@naukri.com",
        ),
    )
    assert result["outcome"] != engine.VERIFIED_OFFER_LETTER


def test_a_payslip_is_not_an_offer_letter():
    """A payslip carries a salary figure, a date and a job title. It is a
    record of existing employment, not an offer."""
    payslip = (
        "Employee Code : 782541 Pay Period : 01/05/2026 To 31/05/2026 "
        "Employee Name : Gumma Gopichand Hire Date : 07/09/2021 Employee Band : U2 "
        "Pay Entity : Tech Mahindra Limited Function : Technical Net Pay 84,500"
    )
    result = engine.classify_message(
        message(
            subject="Documents",
            body="During the initial training period as mentioned in offer letter, your "
                 "performance would be closely monitored.",
            sender_email="gummagopichand@gmail.com",
        ),
        [attachment(filename="29-MAY-2026.pdf", attachment_type="OFFER_LETTER", text=payslip)],
    )
    assert result["outcome"] != engine.VERIFIED_OFFER_LETTER


def test_a_real_appointment_letter_attachment_is_still_verified():
    """The true positive from the same audit must keep working."""
    appointment = (
        "APPOINTMENT LETTER Date: 16/07/2026 Dear Lekkala Swathi, This has reference to "
        "your application and subsequent interviews you have had with Kaivale Technologies "
        "Private Limited. We are pleased to offer you the position of Software Engineer. "
        "Your annual CTC is INR 6,00,000. Your date of joining is 01 Aug 2026."
    )
    result = engine.classify_message(
        message(subject="Welcome to Kaivale Technologies",
                body="Please sign the attached offer letter.",
                sender_email="vanshika@kaivale.example"),
        [attachment(filename="Kaivale Technologies offer Letter_Signed.pdf",
                    attachment_type="OFFER_LETTER", text=appointment)],
    )
    assert result["outcome"] == engine.VERIFIED_OFFER_LETTER
    assert result["confidence"] >= 90


def test_appointment_letter_mentioning_relieving_letters_is_still_verified():
    """Employment terms routinely reference relieving and experience letters.

    Treating that vocabulary as disqualifying rejected the one genuine offer
    letter in the production mailboxes.
    """
    appointment = (
        "APPOINTMENT LETTER Date: 16/07/2026 Dear Lekkala Swathi, This has reference to "
        "your application and subsequent interviews you have had with Kaivale Technologies "
        "Private Limited and their associates. We are pleased to appoint you as an "
        "Sr. Software Engineer, Based in Bangalore. Your employment will be governed by the "
        "following terms and conditions. 1. Monthly Gross Salary Your Annual Salary will be "
        "INR 6,00,000. Your date of joining is 01 Aug 2026. You shall produce the relieving "
        "letter from your previous employer at the time of joining."
    )
    result = engine.classify_message(
        message(subject="Welcome to Kaivale Technologies",
                body="Please sign the attached offer letter.",
                sender_email="vanshika@kaivale.example"),
        [attachment(filename="Kaivale Technologies offer Letter_Signed.pdf",
                    attachment_type="OFFER_LETTER", text=appointment)],
    )
    assert result["outcome"] == engine.VERIFIED_OFFER_LETTER


def test_payslip_has_no_offer_language_so_stays_rejected():
    """The counterpart to the test above: a payslip must still not qualify."""
    payslip = (
        "Employee Code : 782541 Pay Period : 01/05/2026 To 31/05/2026 "
        "Employee Name : Gumma Gopichand Hire Date : 07/09/2021 Net Pay 84,500 "
        "Provident Fund 1,800 Earnings Deductions"
    )
    result = engine.classify_message(
        message(subject="Documents", body="Your offer letter is referenced below."),
        [attachment(filename="29-MAY-2026.pdf", attachment_type="OFFER_LETTER", text=payslip)],
    )
    assert result["outcome"] != engine.VERIFIED_OFFER_LETTER


def test_offer_letter_mentioned_but_not_attached_is_not_verified():
    result = engine.classify_message(
        message(subject="Update", body="Your offer letter will follow shortly."),
    )
    assert result["outcome"] != engine.VERIFIED_OFFER_LETTER


# ── 5. Fake or mismatched sender domain ──────────────────────────────────────

def test_mismatched_sender_domain_is_flagged_without_accusation():
    assessment = engine.assess_authenticity(
        message(sender_email="hr.acme.offers@gmail.com",
                authentication_results="spf=pass; dkim=pass; dmarc=pass"),
        company_domain="acme-corp.example",
    )
    assert assessment["verdict"] == engine.AUTHENTICITY_SUSPICIOUS
    assert assessment["concerns"]
    joined = " ".join(assessment["concerns"]).lower()
    assert "gmail.com" in joined
    # The report describes the mismatch; it never labels anyone a fraudster.
    assert "fraud" not in joined and "scam" not in joined


def test_failed_spf_is_a_concern():
    assessment = engine.assess_authenticity(
        message(authentication_results="spf=fail; dkim=fail; dmarc=fail", received_spf="fail"),
        company_domain="acme-corp.example",
    )
    assert assessment["verdict"] == engine.AUTHENTICITY_SUSPICIOUS
    assert any("SPF" in concern for concern in assessment["concerns"])


def test_recruiting_platform_relay_is_not_suspicious():
    assessment = engine.assess_authenticity(
        message(sender_email="noreply@naukri.com"),
        company_domain="acme-corp.example",
    )
    assert assessment["verdict"] != engine.AUTHENTICITY_SUSPICIOUS
    assert any(item["state"] == "RELAY" for item in assessment["checks"])


def test_reply_to_pointing_off_domain_is_reported():
    assessment = engine.assess_authenticity(
        message(sender_email="hr@acme-corp.example", reply_to_email="collect@other.example"),
        company_domain="acme-corp.example",
    )
    assert any(item["check"] == "REPLY_TO" and item["state"] == "MISMATCH"
               for item in assessment["checks"])
    assert assessment["verdict"] == engine.AUTHENTICITY_SUSPICIOUS


def test_missing_headers_are_unverified_never_pass():
    assessment = engine.assess_authenticity(
        message(authentication_results=None, received_spf=None),
        company_domain=None,
    )
    assert assessment["verdict"] in {engine.AUTHENTICITY_UNVERIFIED, engine.AUTHENTICITY_PARTIAL}
    assert assessment["verdict"] != engine.AUTHENTICITY_PASS
    states = {item["check"]: item["state"] for item in assessment["checks"]}
    assert states["SPF"] == "UNAVAILABLE"
    assert states["REPLY_TO"] == "UNAVAILABLE"


# ── 6. Rejection email ───────────────────────────────────────────────────────

def test_rejection_is_recognised():
    result = engine.classify_message(
        message(subject="Application update",
                body="We regret to inform you that you have not been selected for this position."),
    )
    assert result["outcome"] == engine.REJECTED
    assert result["confidence"] >= 80


def test_position_closed_is_a_rejection():
    result = engine.classify_message(
        message(subject="Role update", body="The position has been closed and we are not proceeding."),
    )
    assert result["outcome"] == engine.REJECTED


# ── 7. Background verification without final selection ───────────────────────

def test_background_verification_is_not_selection_or_joining():
    result = engine.classify_message(
        message(subject="Background verification",
                body="Our verification partner will begin your background verification. "
                     "Please complete the BGV form."),
    )
    assert result["outcome"] == engine.BACKGROUND_VERIFICATION
    assert result["outcome"] != engine.JOINING_CONFIRMED
    assert "not selection or joining confirmation" in result["rationale"].lower()


def test_document_request_alone_is_not_an_outcome():
    result = engine.classify_message(
        message(subject="Documents required",
                body="Please submit your documents and educational certificates."),
    )
    assert result["outcome"] == engine.NOT_RELEVANT
    assert "DOCUMENT_REQUEST" in result["signals"]


def test_recruiter_interest_is_not_selection():
    result = engine.classify_message(
        message(subject="Opportunity",
                body="We came across your profile and have an opening. Would you be interested? "
                     "Please share your updated resume."),
    )
    assert result["outcome"] == engine.NOT_RELEVANT
    assert "RECRUITER_INTEREST" in result["signals"]


# ── 8. Duplicate email / thread ──────────────────────────────────────────────

def test_identical_content_produces_one_signature():
    first = message(subject="Your offer letter", body="Please find your offer letter attached.")
    second = message(subject="Your offer letter", body="Please find your offer letter attached.")
    files = [attachment(checksum="abc123")]
    assert engine.content_signature(first, files) == engine.content_signature(second, files)


def test_different_content_produces_different_signatures():
    first = message(subject="Your offer letter", body="Please find your offer letter attached.")
    second = message(subject="Interview invitation", body="Your interview has been scheduled.")
    assert engine.content_signature(first, []) != engine.content_signature(second, [])


def test_quoted_reply_history_does_not_create_a_second_outcome():
    """Quoted history repeats an earlier outcome; it must not become a new one."""
    reply = engine.classify_message(
        message(subject="Re: Availability",
                body="Thank you, that works for me.\nOn Monday Recruiter wrote:\n"
                     "You have been selected for the position of Data Engineer."),
    )
    assert reply["outcome"] == engine.NOT_RELEVANT


def test_candidate_authored_mail_is_never_a_company_outcome():
    """A candidate writing about their own selection is not a company decision."""
    result = engine.classify_message(
        message(subject="Selected at Acme",
                body="I have been selected for the position and will join next month.",
                message_direction="OUTBOUND"),
    )
    assert result["outcome"] == engine.NOT_RELEVANT
    assert "OUTBOUND" in result["signals"]


def test_offer_letter_joining_date_is_part_of_the_offer_not_a_joining_confirmation():
    """An offer letter always states a joining date. That is an offer term."""
    result = engine.classify_message(
        message(subject="Your offer letter", body="Please find your offer letter attached."),
        [attachment(filename="Offer_Letter_Acme.pdf", attachment_type="OFFER_LETTER",
                    text=OFFER_PDF_TEXT)],
    )
    assert result["outcome"] == engine.VERIFIED_OFFER_LETTER


def test_a_separate_joining_confirmation_mail_is_a_joining_confirmation():
    result = engine.classify_message(
        message(subject="Joining confirmation",
                body="Your joining is confirmed. Your date of joining is 01 Sep 2026. "
                     "Please report to the Hyderabad office."),
    )
    assert result["outcome"] == engine.JOINING_CONFIRMED


# ── 9. Forwarded offer email ─────────────────────────────────────────────────

def test_forwarded_offer_is_not_independently_authenticated():
    assessment = engine.assess_authenticity(
        message(subject="Fwd: Your offer letter",
                body="---------- Forwarded message ---------\nFrom: hr@acme-corp.example",
                sender_email="candidate@gmail.com"),
        company_domain="acme-corp.example",
        mailbox_email="candidate@gmail.com",
    )
    assert assessment["forwarded"] is True
    assert assessment["verdict"] != engine.AUTHENTICITY_PASS
    assert any("forward" in note.lower() for note in assessment["notes"] + assessment["concerns"])


# ── 10. Attachment parsing failure ───────────────────────────────────────────

def test_unreadable_offer_attachment_requires_review():
    result = engine.classify_message(
        message(subject="Your offer letter", body="Please find your offer letter attached."),
        [attachment(filename="Offer_Letter.pdf", attachment_type="OFFER_LETTER",
                    extraction_status="FAILED", text="")],
    )
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED
    assert "ATTACHMENT_UNREADABLE" in result["signals"]


# ── 15. Conflicting selection and rejection ──────────────────────────────────

def test_conflicting_selection_and_rejection_in_one_mail_needs_review():
    result = engine.classify_message(
        message(subject="Update",
                body="We are pleased to offer you the role. However we regret to inform you "
                     "that you have not been selected."),
    )
    assert result["outcome"] == engine.MANUAL_REVIEW_REQUIRED
    assert result["manual_review_required"] is True


def test_conflicting_outcomes_across_mails_are_detected_per_company():
    conflicts = engine.detect_conflicts([
        {"outcome": engine.FINAL_SELECTION, "company_domain": "acme-corp.example"},
        {"outcome": engine.REJECTED, "company_domain": "acme-corp.example"},
    ])
    assert conflicts and "acme-corp.example" in conflicts[0]


def test_selection_at_one_company_and_rejection_at_another_is_not_a_conflict():
    conflicts = engine.detect_conflicts([
        {"outcome": engine.FINAL_SELECTION, "company_domain": "acme-corp.example"},
        {"outcome": engine.REJECTED, "company_domain": "other-corp.example"},
    ])
    assert conflicts == []


# ── 16. No relevant emails ───────────────────────────────────────────────────

def test_job_alert_noise_is_not_an_outcome():
    result = engine.classify_message(
        message(subject="Naukri job alert", body="New jobs for you matching your profile. Apply now."),
    )
    assert result["outcome"] == engine.NOT_RELEVANT


def test_empty_mailbox_has_no_strongest_outcome():
    assert engine.strongest([]) is None


def test_application_acknowledgement_is_not_an_outcome():
    result = engine.classify_message(
        message(subject="Application received",
                body="Thank you for applying. Your application is under review."),
    )
    assert result["outcome"] == engine.NOT_RELEVANT


# ── 17. Multiple companies for one candidate ─────────────────────────────────

def test_strongest_outcome_across_companies_wins():
    findings = [
        {"outcome": engine.INTERVIEW_INVITE, "confidence": 90, "company_domain": "a.example",
         "received_at": "2026-07-01"},
        {"outcome": engine.VERIFIED_OFFER_LETTER, "confidence": 92, "company_domain": "b.example",
         "received_at": "2026-07-20"},
        {"outcome": engine.REJECTED, "confidence": 88, "company_domain": "c.example",
         "received_at": "2026-07-25"},
    ]
    best = engine.strongest(findings)
    assert best["outcome"] == engine.VERIFIED_OFFER_LETTER
    counts = engine.outcome_counts(findings)
    assert counts[engine.INTERVIEW_INVITE] == 1 and counts[engine.REJECTED] == 1


def test_not_relevant_findings_never_become_the_strongest_outcome():
    assert engine.strongest([
        {"outcome": engine.NOT_RELEVANT, "confidence": 99, "received_at": "2026-07-01"},
    ]) is None


# ── Ranking guarantees the specification calls out ───────────────────────────

@pytest.mark.parametrize("lower,higher", [
    (engine.INTERVIEW_INVITE, engine.FINAL_SELECTION),
    (engine.NEXT_ROUND, engine.OFFER_INDICATION),
    (engine.BACKGROUND_VERIFICATION, engine.JOINING_CONFIRMED),
    (engine.OFFER_INDICATION, engine.VERIFIED_OFFER_LETTER),
    (engine.SHORTLISTED, engine.FINAL_SELECTION),
])
def test_outcome_ranking_respects_hiring_progression(lower, higher):
    assert engine.OUTCOME_RANK[lower] < engine.OUTCOME_RANK[higher]


def test_domain_helpers_collapse_mail_subdomains():
    assert engine.registrable_domain("careers.acme-corp.example") == "acme-corp.example"
    assert engine.registrable_domain("mail.acme.co.in") == "acme.co.in"
    assert engine.domain_of("HR@Acme-Corp.Example") == "acme-corp.example"


def test_authentication_header_parsing():
    parsed = engine.parse_authentication(
        "mx.google.com; dkim=pass header.i=@acme.example; spf=softfail; dmarc=fail"
    )
    assert parsed == {"dkim": "pass", "spf": "softfail", "dmarc": "fail"}
