"""Who sent it decides how much a finding is worth.

Every fixture here is a real message from the Production mailboxes. Together
they were reported as an offer indication, a rejection and three next rounds
for one candidate, and the audit recommended setting his status to "Offer
Received". Not one of them came from a hiring company.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recruitment_mail_audit as engine  # noqa: E402


def message(**overrides):
    base = {
        "subject": "",
        "body": "",
        "sender_email": "alerts@jobs.shine.com",
        "authentication_results": None,
        "received_spf": None,
        "message_direction": None,
    }
    base.update(overrides)
    return base


# ── The five mails that produced a false "Offer Received" ────────────────────

SHINE_TOP_APPLICANT = message(
    subject="You are a Top Applicant! Details Required",
    body="Exciting Career Opportunities. Dear Abilash, I am a Sr. Recruitment Consultant "
         "with Stravya Hiring Solutions Limited. While reviewing top talents on Shine.com, "
         "your profile has been shortlisted for our top client for a position in your "
         "industry. However, we came up short on details. manager 3rd round : skip level "
         "interview 4th round : offer roll out you need to complete pending details today "
         "for us to start the process. Best regards, Aditi",
)

NAUKRI_JOB_INVITE = message(
    subject="Job | Backend Developer - Node.js/TypeScript - 5+ Years - Gurugram (Hybrid) "
            "in Crescendo Global",
    body="Job invite from recruiter. Apply now! You've been chosen from a large pool of "
         "jobseekers to apply for this job. Abilash Perla, you're invited to apply to this "
         "job. Hiring for Crescendo Global. Posted by Crescendo Global Leadership Hiring "
         "India. Job description. Not Disclosed. As we get back to each candidate, please "
         "assume that your profile has not been shortlisted in case you don't hear back "
         "from us within 1 week. Your patience is appreciated.",
    sender_email="rohit.kumar@naukri.com",
)

SHINE_PROFILE_DETAILS = message(
    subject="Interview 1st Round - Profile Details Required",
    body="Executive Search Consultancy. Looking forward to moving your application to the "
         "next round. Please confirm your profile details. Warm regards, Anjali Kapoor, "
         "Senior Recruiter, Skill Scout Consultancy.",
)

SHINE_VERIFY_DETAILS = message(
    subject="Urgent Vacancy - Please Verify Profile Details",
    body="Urgent Vacancy - Verify Profile Details. Please verify profile details to proceed. "
         "Navrox Hiring Private limited.",
)

SHINE_SHORTLISTED = message(
    subject="Your Profile is Shortlisted | Please Confirm Details",
    body="Dear Abilash, While reviewing top talents on Shine.com, your profile has been "
         "shortlisted for our top client. Please confirm details now to proceed.",
)


@pytest.mark.parametrize("mail,label", [
    (SHINE_TOP_APPLICANT, "top applicant"),
    (NAUKRI_JOB_INVITE, "job invite"),
    (SHINE_PROFILE_DETAILS, "profile details"),
    (SHINE_VERIFY_DETAILS, "verify details"),
    (SHINE_SHORTLISTED, "portal shortlist"),
])
def test_no_portal_campaign_produces_a_selection_outcome(mail, label):
    result = engine.classify_message(mail)
    assert result["outcome"] == engine.NOT_RELEVANT, (
        f"{label} produced {result['outcome']}: {result['rationale']}"
    )


def test_top_applicant_is_never_an_offer():
    result = engine.classify_message(SHINE_TOP_APPLICANT)
    assert result["outcome"] not in {engine.OFFER_INDICATION, engine.VERIFIED_OFFER_LETTER}
    assert "BULK_CAMPAIGN" in result["signals"]


def test_a_job_advert_is_never_a_rejection():
    """"Assume you were not shortlisted if you don't hear back" is a policy
    sentence in an advert, not this candidate's rejection."""
    result = engine.classify_message(NAUKRI_JOB_INVITE)
    assert result["outcome"] != engine.REJECTED


def test_profile_details_request_is_not_a_next_round():
    result = engine.classify_message(SHINE_PROFILE_DETAILS)
    assert result["outcome"] != engine.NEXT_ROUND
    assert result["outcome"] == engine.NOT_RELEVANT


def test_the_reason_names_the_portal_so_the_exclusion_is_explainable():
    result = engine.classify_message(SHINE_TOP_APPLICANT)
    assert "shine.com" in result["rationale"]


# ── Generic rejection boilerplate ────────────────────────────────────────────

def test_generic_boilerplate_alone_is_not_a_rejection():
    result = engine.classify_message(message(
        subject="Thank you for your interest",
        body="If you don't hear back from us within two weeks, please assume that your "
             "profile has not been shortlisted.",
        sender_email="careers@acme-corp.example",
    ))
    assert result["outcome"] != engine.REJECTED


def test_a_specific_rejection_is_still_a_rejection():
    """The guard must not swallow a real one."""
    result = engine.classify_message(message(
        subject="Update on your application - Senior Data Engineer",
        body="We regret to inform you that you have not been selected for the position of "
             "Senior Data Engineer following your final interview.",
        sender_email="careers@acme-corp.example",
    ))
    assert result["outcome"] == engine.REJECTED


def test_a_real_company_offer_still_survives():
    """The portal guard must not suppress genuine company mail."""
    result = engine.classify_message(message(
        subject="Your offer letter",
        body="We are pleased to offer you the position of Senior Data Engineer. "
             "Your annual CTC is INR 24,00,000 and your date of joining is 01 Sep 2026.",
        sender_email="hr@acme-corp.example",
    ))
    assert result["outcome"] in {engine.OFFER_INDICATION, engine.VERIFIED_OFFER_LETTER}


# ── Source classification ────────────────────────────────────────────────────

@pytest.mark.parametrize("sender,expected", [
    ("alerts@jobs.shine.com", engine.SOURCE_PORTAL),
    ("rohit.kumar@naukri.com", engine.SOURCE_PORTAL),
    ("noreply@linkedin.com", engine.SOURCE_PORTAL),
    ("hr@acme-corp.example", engine.SOURCE_COMPANY),
    ("recruiter@gmail.com", engine.SOURCE_PERSONAL),
    ("ankit@talentployer.example", engine.SOURCE_THIRD_PARTY),
])
def test_source_is_identified_from_the_sender(sender, expected):
    assert engine.classify_source(sender, "acme-corp.example") == expected


def test_matching_company_domain_is_the_company_itself():
    assert engine.classify_source("careers@kaivale.com", "kaivale.com") == engine.SOURCE_COMPANY


# ── Evidence strength ────────────────────────────────────────────────────────

def test_portal_evidence_is_always_weak():
    assert engine.evidence_strength(
        source=engine.SOURCE_PORTAL, authenticity=engine.AUTHENTICITY_PASS, bulk=False,
    ) == engine.STRENGTH_WEAK


def test_bulk_campaign_evidence_is_always_weak():
    assert engine.evidence_strength(
        source=engine.SOURCE_COMPANY, authenticity=engine.AUTHENTICITY_PASS, bulk=True,
    ) == engine.STRENGTH_WEAK


def test_authenticated_company_mail_is_strong():
    assert engine.evidence_strength(
        source=engine.SOURCE_COMPANY, authenticity=engine.AUTHENTICITY_PASS, bulk=False,
    ) == engine.STRENGTH_STRONG


def test_unverified_company_mail_is_only_moderate():
    assert engine.evidence_strength(
        source=engine.SOURCE_COMPANY, authenticity=engine.AUTHENTICITY_UNVERIFIED, bulk=False,
    ) == engine.STRENGTH_MODERATE


def test_suspicious_authenticity_is_weak_whatever_the_source():
    assert engine.evidence_strength(
        source=engine.SOURCE_COMPANY, authenticity=engine.AUTHENTICITY_SUSPICIOUS, bulk=False,
    ) == engine.STRENGTH_WEAK


def test_agency_mail_is_weak_without_a_document():
    assert engine.evidence_strength(
        source=engine.SOURCE_THIRD_PARTY, authenticity=engine.AUTHENTICITY_PASS, bulk=False,
    ) == engine.STRENGTH_WEAK
    assert engine.evidence_strength(
        source=engine.SOURCE_THIRD_PARTY, authenticity=engine.AUTHENTICITY_PASS, bulk=False,
        has_attachment_proof=True,
    ) == engine.STRENGTH_MODERATE


# ── The approval gate ────────────────────────────────────────────────────────

def approvable(**overrides):
    base = {
        "outcome": engine.VERIFIED_OFFER_LETTER,
        "evidence_strength": engine.STRENGTH_STRONG,
        "source_type": engine.SOURCE_COMPANY,
        "authenticity": engine.AUTHENTICITY_PASS,
        "company_name": "Kaivale Technologies",
        "company_domain": "kaivale.com",
        "job_title": "Sr. Software Engineer",
        "confidence": 92.0,
    }
    base.update(overrides)
    return base


def test_a_fully_verified_company_offer_is_approvable():
    assert engine.approval_eligibility(approvable())["eligible"] is True


@pytest.mark.parametrize("override,fragment", [
    ({"source_type": engine.SOURCE_PORTAL}, "not confirmed to be the hiring company"),
    ({"evidence_strength": engine.STRENGTH_WEAK}, "not strong enough"),
    ({"authenticity": engine.AUTHENTICITY_SUSPICIOUS}, "authenticity"),
    ({"company_name": "", "company_domain": ""}, "identified company"),
    ({"job_title": ""}, "identified role"),
    ({"confidence": 60.0}, "Confidence is below"),
])
def test_each_missing_condition_blocks_approval(override, fragment):
    result = engine.approval_eligibility(approvable(**override))
    assert result["eligible"] is False
    assert any(fragment in blocker for blocker in result["blockers"])
    assert result["message"] == engine.INSUFFICIENT_EVIDENCE_MESSAGE


def test_a_later_conflicting_message_blocks_approval():
    result = engine.approval_eligibility(approvable(), later_conflict=True)
    assert result["eligible"] is False
    assert any("conflicts" in blocker for blocker in result["blockers"])


def test_the_blocked_message_is_the_wording_the_operator_asked_for():
    result = engine.approval_eligibility(approvable(source_type=engine.SOURCE_PORTAL))
    assert result["message"] == (
        "Needs manual review — evidence is insufficient for a status change."
    )
