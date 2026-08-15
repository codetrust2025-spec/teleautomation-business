"""Evidence engine for the candidate mail outcome audit.

This module answers one question for a single email: *what did the company
actually tell this candidate?*  It is deliberately independent of the live
detection pipeline — it re-reads the stored subject, body, thread context and
extracted attachment text and reaches its own conclusion, so that comparing the
two surfaces what the pipeline missed or got wrong.

Every function here is pure and side-effect free.  Nothing in this module
touches Gmail, the database, or candidate records.

Design rules that the classifier enforces, because each one is a way a naive
keyword matcher reports a promotion that never happened:

* interview scheduling is not final selection
* a next-round email is not an offer
* a document request alone is not final selection
* background verification alone is not joining confirmation
* recruiter interest is not proof of selection
* an offer letter is only "verified" when real offer details are present
* conflicting or incomplete evidence stays MANUAL_REVIEW_REQUIRED
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Iterable


# ── Outcome taxonomy ─────────────────────────────────────────────────────────

INTERVIEW_INVITE = "INTERVIEW_INVITE"
INTERVIEW_RESCHEDULED = "INTERVIEW_RESCHEDULED"
INTERVIEW_CANCELLED = "INTERVIEW_CANCELLED"
NEXT_ROUND = "NEXT_ROUND"
SHORTLISTED = "SHORTLISTED"
FINAL_SELECTION = "FINAL_SELECTION"
OFFER_INDICATION = "OFFER_INDICATION"
VERIFIED_OFFER_LETTER = "VERIFIED_OFFER_LETTER"
JOINING_CONFIRMED = "JOINING_CONFIRMED"
BACKGROUND_VERIFICATION = "BACKGROUND_VERIFICATION"
REJECTED = "REJECTED"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
NOT_RELEVANT = "NOT_RELEVANT"

OUTCOMES = (
    INTERVIEW_INVITE, INTERVIEW_RESCHEDULED, INTERVIEW_CANCELLED, NEXT_ROUND,
    SHORTLISTED, FINAL_SELECTION, OFFER_INDICATION, VERIFIED_OFFER_LETTER,
    JOINING_CONFIRMED, BACKGROUND_VERIFICATION, REJECTED,
    MANUAL_REVIEW_REQUIRED, NOT_RELEVANT,
)

# How far through the hiring process an outcome places the candidate.  Used to
# pick the strongest verified outcome per candidate.  REJECTED sits above the
# interview stages because it is a real company decision, but below selection —
# a candidate holding both is a conflict, reported explicitly rather than ranked
# away.
OUTCOME_RANK = {
    NOT_RELEVANT: 0,
    MANUAL_REVIEW_REQUIRED: 5,
    INTERVIEW_CANCELLED: 10,
    INTERVIEW_RESCHEDULED: 15,
    INTERVIEW_INVITE: 20,
    NEXT_ROUND: 30,
    SHORTLISTED: 35,
    REJECTED: 40,
    BACKGROUND_VERIFICATION: 50,
    FINAL_SELECTION: 60,
    OFFER_INDICATION: 70,
    VERIFIED_OFFER_LETTER: 80,
    JOINING_CONFIRMED: 90,
}

# Outcomes that mean the company committed to hiring this candidate.  Paired
# with REJECTED on the same candidate this is a conflict worth a human.
POSITIVE_DECISION_OUTCOMES = frozenset({
    FINAL_SELECTION, OFFER_INDICATION, VERIFIED_OFFER_LETTER,
    JOINING_CONFIRMED, BACKGROUND_VERIFICATION,
})

MEANINGFUL_OUTCOMES = frozenset(set(OUTCOMES) - {NOT_RELEVANT, MANUAL_REVIEW_REQUIRED})


# ── Audit modes ──────────────────────────────────────────────────────────────
#
# Selection and interview-slot results answer different questions and were
# being totalled together, so a mailbox full of interview invitations read as
# hiring progress. The two are partitioned here and never mixed.

MODE_SELECTION = "SELECTION"
MODE_INTERVIEW = "INTERVIEW"
MODES = (MODE_SELECTION, MODE_INTERVIEW)

# NEXT_ROUND sits on the selection side beside SHORTLISTED: both say the
# company advanced this candidate. The interview mode is about slot mechanics —
# what was booked, blocked or missed — not about progression.
SELECTION_OUTCOMES = frozenset({
    VERIFIED_OFFER_LETTER, FINAL_SELECTION, OFFER_INDICATION, JOINING_CONFIRMED,
    BACKGROUND_VERIFICATION, SHORTLISTED, NEXT_ROUND, REJECTED,
    MANUAL_REVIEW_REQUIRED,
})
INTERVIEW_OUTCOMES = frozenset({
    INTERVIEW_INVITE, INTERVIEW_RESCHEDULED, INTERVIEW_CANCELLED,
})

# Booking outcomes come from the auto-booking pipeline, not from mail text.
BOOKING_AUTO_BOOKED = "INTERVIEW_AUTO_BOOKED"
BOOKING_BLOCKED = "BOOKING_BLOCKED"
BOOKING_DUPLICATE_IGNORED = "DUPLICATE_BOOKING_IGNORED"
BOOKING_SLOT_CONFLICT = "SLOT_CONFLICT"
BOOKING_MISSING_SCHEDULE = "MISSING_DATE_OR_TIME"
BOOKING_HISTORICAL_SKIPPED = "HISTORICAL_NOT_BOOKED"
INVITE_UNPROCESSED = "MISSED_OR_UNPROCESSED_INVITE"

BOOKING_OUTCOMES = (
    BOOKING_AUTO_BOOKED, BOOKING_BLOCKED, BOOKING_DUPLICATE_IGNORED,
    BOOKING_SLOT_CONFLICT, BOOKING_MISSING_SCHEDULE,
    BOOKING_HISTORICAL_SKIPPED, INVITE_UNPROCESSED,
)

INTERVIEW_MODE_CATEGORIES = (
    INTERVIEW_INVITE, BOOKING_AUTO_BOOKED, INTERVIEW_RESCHEDULED,
    INTERVIEW_CANCELLED, BOOKING_BLOCKED, BOOKING_DUPLICATE_IGNORED,
    BOOKING_SLOT_CONFLICT, BOOKING_MISSING_SCHEDULE, INVITE_UNPROCESSED,
    BOOKING_HISTORICAL_SKIPPED,
)
SELECTION_MODE_CATEGORIES = (
    VERIFIED_OFFER_LETTER, FINAL_SELECTION, OFFER_INDICATION, JOINING_CONFIRMED,
    BACKGROUND_VERIFICATION, SHORTLISTED, NEXT_ROUND, REJECTED,
    MANUAL_REVIEW_REQUIRED,
)


# ── Selection-audit cleanup ──────────────────────────────────────────────────
#
# A finding is never deleted. Suppression marks it as not counting toward the
# selection totals or a candidate's strongest outcome, records why, and leaves
# the mail, the evidence and the audit history exactly as they were.

SUPPRESS_IRRELEVANT = "IRRELEVANT"
SUPPRESS_DUPLICATE = "DUPLICATE"
SUPPRESS_SUPERSEDED = "SUPERSEDED"
SUPPRESS_WRONG_MODE = "WRONG_AUDIT_MODE"
SUPPRESSION_REASONS = (
    SUPPRESS_IRRELEVANT, SUPPRESS_DUPLICATE, SUPPRESS_SUPERSEDED, SUPPRESS_WRONG_MODE,
)

# Outcomes that on their own prove a company decided to hire this candidate.
# Background verification and document checks accompany such a decision; they
# do not establish one, so they only survive cleanup alongside real evidence.
SELECTION_PROOF_OUTCOMES = frozenset({
    OFFER_INDICATION, VERIFIED_OFFER_LETTER, FINAL_SELECTION, JOINING_CONFIRMED,
})
SUPPORTING_ONLY_OUTCOMES = frozenset({BACKGROUND_VERIFICATION})


def _company_key(finding: dict[str, Any]) -> str:
    return (
        registrable_domain(str(finding.get("company_domain") or ""))
        or str(finding.get("company_name") or "").strip().lower()
        or registrable_domain(str(finding.get("sender_domain") or ""))
        or "unknown"
    )


def _received_key(finding: dict[str, Any]) -> str:
    return str(finding.get("received_at") or "")


def selection_suppressions(findings: Iterable[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Decide which of one candidate's findings the selection audit should skip.

    Returns {finding_id: {"reason": ..., "detail": ...}} for suppressed rows
    only. Pure: it reads findings and returns a decision, nothing else.
    """
    items = sorted(findings, key=_received_key)
    decisions: dict[str, dict[str, str]] = {}

    def suppress(finding: dict[str, Any], reason: str, detail: str) -> None:
        key = str(finding.get("id") or "")
        if key and key not in decisions:
            decisions[key] = {"reason": reason, "detail": detail}

    # Pass 1 — findings that never belonged in this audit at all.
    for finding in items:
        outcome = str(finding.get("outcome") or "")
        if outcome in INTERVIEW_OUTCOMES:
            suppress(finding, SUPPRESS_WRONG_MODE,
                     "Interview-slot result; counted in the Interview Slot Audit instead.")
            continue
        if outcome == NOT_RELEVANT:
            signals = finding.get("signals") or []
            label = ", ".join(str(item) for item in signals) if signals else "no outcome language"
            suppress(finding, SUPPRESS_IRRELEVANT,
                     f"Carries no selection outcome ({label}).")
            continue
        evidence = finding.get("evidence")
        if isinstance(evidence, (list, tuple)) and not evidence:
            suppress(finding, SUPPRESS_IRRELEVANT,
                     "No evidence was recorded to support this outcome.")

    live = [f for f in items if str(f.get("id") or "") not in decisions]

    # Pass 2 — the same mail counted twice. A forwarded copy, a thread reply
    # carrying the original attachment, or two near-identical files all produce
    # a second finding for one real event.
    seen: dict[tuple, str] = {}
    for finding in live:
        signature = str(finding.get("content_signature") or "")
        fingerprint = str(finding.get("attachment_fingerprint") or "")
        thread = str(finding.get("provider_thread_id") or "")
        outcome = str(finding.get("outcome") or "")
        keys = []
        if signature:
            keys.append(("content", signature))
        if fingerprint:
            keys.append(("attachment", fingerprint, outcome))
        if thread:
            # Deliberately not scoped by company. One thread is one
            # conversation, and a reply that attaches the signed copy back
            # resolves to the candidate's own domain rather than the
            # recruiter's, so adding the company split one offer into two.
            keys.append(("thread", thread, outcome))
        matched = next((seen[key] for key in keys if key in seen), None)
        if matched:
            suppress(finding, SUPPRESS_DUPLICATE,
                     f"Same {outcome.lower().replace('_', ' ')} already counted from message {matched}.")
            continue
        for key in keys:
            seen.setdefault(key, str(finding.get("provider_message_id") or finding.get("id") or ""))

    live = [f for f in items if str(f.get("id") or "") not in decisions]

    # Pass 3 — support-only evidence with nothing it can support.
    by_company_proof: dict[str, bool] = {}
    for finding in live:
        if str(finding.get("outcome") or "") in SELECTION_PROOF_OUTCOMES:
            by_company_proof[_company_key(finding)] = True
    for finding in live:
        outcome = str(finding.get("outcome") or "")
        if outcome in SUPPORTING_ONLY_OUTCOMES and not by_company_proof.get(_company_key(finding)):
            suppress(finding, SUPPRESS_IRRELEVANT,
                     "Background or document verification with no offer or selection evidence "
                     "from the same company.")

    live = [f for f in items if str(f.get("id") or "") not in decisions]

    # Pass 4 — earlier, weaker statements the company has since overtaken.
    # An offer indication followed by that company's verified offer letter is
    # history, not a second outcome.
    for finding in live:
        outcome = str(finding.get("outcome") or "")
        rank = OUTCOME_RANK.get(outcome, 0)
        company = _company_key(finding)
        received = _received_key(finding)
        stronger = next(
            (
                other for other in live
                if other is not finding
                and _company_key(other) == company
                and OUTCOME_RANK.get(str(other.get("outcome") or ""), 0) > rank
                and _received_key(other) >= received
                and str(other.get("outcome") or "") in SELECTION_PROOF_OUTCOMES
            ),
            None,
        )
        if stronger is not None:
            suppress(
                finding, SUPPRESS_SUPERSEDED,
                f"Superseded by a later {str(stronger.get('outcome') or '').lower().replace('_', ' ')} "
                f"from {company}.",
            )

    return decisions


def application_key(finding: dict[str, Any]) -> str:
    """Identify the application a finding belongs to.

    One company plus one role is one application. Outcomes from different
    companies are separate lifecycles and must never be merged, so a later
    rejection from company B cannot cancel an offer from company A.
    """
    company = _company_key(finding)
    role = re.sub(r"[^a-z0-9]+", "-", str(finding.get("job_title") or "").strip().lower()).strip("-")
    return f"{company}:{role}" if role else company


def normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().upper()
    return mode if mode in MODES else MODE_SELECTION


def outcomes_for_mode(mode: str) -> frozenset:
    """Mail outcomes belonging to one audit mode. The two never overlap."""
    return SELECTION_OUTCOMES if normalize_mode(mode) == MODE_SELECTION else INTERVIEW_OUTCOMES


def mode_for_outcome(outcome: str) -> str | None:
    if outcome in SELECTION_OUTCOMES:
        return MODE_SELECTION
    if outcome in INTERVIEW_OUTCOMES or outcome in BOOKING_OUTCOMES:
        return MODE_INTERVIEW
    return None


def booking_outcome(row: dict[str, Any]) -> str:
    """Classify one interview_auto_booking_audit row.

    Ordered by what an operator most needs to know: a booking that happened,
    then the specific reason one did not. Derived from the failure code and the
    duplicate/conflict checks rather than the display status, because the
    status string merges causes that mean different things.
    """
    status = str(row.get("booking_status") or "").strip().upper()
    failure = str(row.get("failure_code") or "").strip().upper()
    duplicate = str(row.get("duplicate_check_status") or "").strip().upper()
    conflict = str(row.get("conflict_check_status") or "").strip().upper()

    if row.get("auto_booked") or status in {"AUTO BOOKED", "APPROVED & BOOKED", "BOOKED"}:
        return BOOKING_AUTO_BOOKED
    if duplicate == "DUPLICATE" or failure == "DUPLICATE_BOOKING" or status == "DUPLICATE IGNORED":
        return BOOKING_DUPLICATE_IGNORED
    if conflict == "CONFLICT" or failure == "SLOT_CONFLICT":
        return BOOKING_SLOT_CONFLICT
    if failure in {"INCOMPLETE_SCHEDULE", "HISTORICAL_SCHEDULE_INCOMPLETE"}:
        return BOOKING_MISSING_SCHEDULE
    # A past interview was seen and deliberately not booked. Calling that
    # "blocked" would report a failure where the pipeline behaved correctly.
    if failure == "PAST_INTERVIEW" or status == "HISTORICAL SKIPPED":
        return BOOKING_HISTORICAL_SKIPPED
    if status in {"BLOCKED", "REVIEW REQUIRED"} or failure:
        return BOOKING_BLOCKED
    if status == "CANCELLED":
        return INTERVIEW_CANCELLED
    return BOOKING_BLOCKED

AUTHENTICITY_PASS = "PASS"
AUTHENTICITY_PARTIAL = "PARTIAL"
AUTHENTICITY_UNVERIFIED = "UNVERIFIED"
AUTHENTICITY_SUSPICIOUS = "SUSPICIOUS"


# ── Text preparation ─────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# Quoted replies and signature blocks repeat earlier outcomes verbatim.  Reading
# them as fresh evidence is how one interview invite becomes five.
_QUOTED = re.compile(
    r"(?im)^\s*(?:on .{0,120}wrote:|-{2,}\s*original message\s*-{2,}"
    r"|from:\s|sent from my |unsubscribe|confidentiality notice)"
)
_FORWARD_MARKER = re.compile(
    r"(?i)(-{3,}\s*forwarded message\s*-{3,}|^\s*fwd:|^\s*fw:|begin forwarded message)"
)


def normalize(value: Any) -> str:
    """Flatten HTML or plain text into lowercase single-spaced text."""
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    return _WS.sub(" ", text).strip().lower()


def visible_text(value: Any) -> str:
    """Normalized text with quoted replies and signatures removed."""
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    text = _QUOTED.split(text, maxsplit=1)[0]
    return _WS.sub(" ", text).strip().lower()


def _phrase(pattern: str) -> re.Pattern:
    """Compile a phrase so it matches on word boundaries, spacing-insensitive."""
    parts = [re.escape(word) for word in pattern.split()]
    return re.compile(r"\b" + r"\W+".join(parts) + r"\b", re.IGNORECASE)


def _compile(groups: Iterable[tuple[str, tuple[str, ...]]]) -> list[tuple[str, list[re.Pattern]]]:
    return [(name, [_phrase(item) for item in phrases]) for name, phrases in groups]


# ── Signal vocabulary ────────────────────────────────────────────────────────
#
# Phrases are the entry point, never the verdict.  Each candidate outcome is
# confirmed or downgraded afterwards by structural checks over the full text.

_JOINING_PHRASES = (
    "your date of joining", "your joining date is", "date of joining is",
    "joining is confirmed", "joining has been confirmed", "confirmed your joining",
    "please report for joining", "report for joining on", "reporting date is",
    "welcome aboard", "welcome to the team", "welcome to the organization",
    "first day at", "your start date is", "date of commencement",
)
# Deliberately possessive or delivery-shaped. A bare "offer letter" is one of
# the most common phrases in recruiter screening mail ("offer letter received
# - y/n", "must have valid employment documents: offer letter, relieving
# letter"), and matching it turned questionnaires and job ads into offers.
_OFFER_LETTER_PHRASES = (
    "your offer letter", "offer letter attached", "attached offer letter",
    "please find your offer letter", "find the attached offer letter",
    "your offer letter is attached", "sign the attached offer letter",
    "releasing your offer letter", "release your offer letter",
    "offer letter has been released", "offer letter is released",
    "letter of appointment", "appointment letter attached",
    "your appointment letter", "offer of employment",
    "we are pleased to attach", "enclosed is your offer",
)

# Text an actual offer or appointment letter contains. A document is only
# treated as an offer document when it reads like one: a payslip carries a
# salary figure and a date too, which is not the same thing.
_OFFER_DOC_CONTENT_PHRASES = (
    "offer of employment", "appointment letter", "letter of appointment",
    "we are pleased to offer", "pleased to appoint", "offer you the position",
    "offer you employment", "terms and conditions of employment",
    "your date of joining", "date of joining", "annual ctc",
    "compensation structure", "this offer is subject to",
    "we are delighted to offer", "employment offer",
)
# Documents that look financial but are records of existing employment.
_NON_OFFER_DOC_PHRASES = (
    "pay period", "payslip", "pay slip", "salary slip", "employee code",
    "net pay", "earnings deductions", "provident fund", "form 16",
    "relieving letter", "experience letter", "service certificate",
)

# Recruiter screening forms. These ask the candidate to state their own status
# and legitimately contain every offer word without conveying any decision.
_QUESTIONNAIRE_PHRASES = (
    "current ctc", "expected ctc", "notice period", "offer in hand",
    "offer letter received", "holding any offer", "any offer in hand",
    "total experience", "relevant experience", "date of birth",
    "pan number", "share your details", "fill the below details",
    "kindly share the below", "please share the following",
    "must have valid employment documents", "share your updated profile",
)
_OFFER_INDICATION_PHRASES = (
    "pleased to offer you", "delighted to offer you", "happy to offer you",
    "would like to offer you", "intent to offer", "letter of intent",
    "planning to release your offer", "offer is being processed",
    "offer is under preparation", "preparing your offer", "offer has been approved",
    "we are extending an offer", "rolling out your offer", "offer roll out",
)
_SELECTION_PHRASES = (
    "you have been selected", "you are selected", "selected for the position",
    "selected for the role", "congratulations on your selection",
    "final selection", "selection has been confirmed", "finally selected",
    "we are pleased to inform you that you have been selected",
    "your candidature has been selected", "cleared all the rounds",
    "successfully cleared all rounds",
)
_SHORTLIST_PHRASES = (
    "you have been shortlisted", "your profile has been shortlisted",
    "shortlisted for the role", "shortlisted for the position",
    "profile is shortlisted", "we have shortlisted your",
    # "provisionally" sits between "profile is" and "shortlisted", so the
    # phrases above miss the wording agencies actually send. Kept here as well
    # as in the agent's signals so the audit engine and the notification path
    # agree on what counts as a shortlist.
    "provisionally shortlisted", "candidature has been shortlisted",
    "you are shortlisted", "shortlisted for further discussion",
)
_NEXT_ROUND_PHRASES = (
    "next round", "next stage", "subsequent round", "following round",
    "moving you to the next", "moving forward to the next",
    "progress to the next", "advanced to the next", "cleared the first round",
    "cleared the technical round", "cleared the round", "you have cleared",
    "qualified for the next", "shortlisted for the next interview",
    "second round", "third round", "final round", "next level of interview",
)
_INTERVIEW_PHRASES = (
    "interview invitation", "invitation for interview", "interview scheduled",
    "interview has been scheduled", "schedule your interview",
    "interview is confirmed", "interview confirmation", "your interview with",
    "technical interview", "technical round", "managerial round", "hr round",
    "hr interview", "screening call", "discussion has been scheduled",
    "we would like to invite you", "invite you for an interview",
    "please join the interview", "interview details",
)
_RESCHEDULE_PHRASES = (
    "interview has been rescheduled", "rescheduled your interview",
    "reschedule the interview", "rescheduling the interview",
    "new interview time", "revised interview", "interview moved to",
    "changed the interview", "interview timing has changed", "rescheduled to",
)
_CANCEL_PHRASES = (
    "interview has been cancelled", "interview is cancelled",
    "cancelling the interview", "cancel the interview",
    "interview stands cancelled", "calling off the interview",
    "interview has been called off", "we are cancelling",
)
_BGV_PHRASES = (
    "background verification", "background check", "pre employment verification",
    "pre-employment verification", "employment verification",
    "bgv process", "bgv form", "verification partner", "antecedent verification",
)
_DOCUMENT_PHRASES = (
    "document verification", "submit your documents", "submit the documents",
    "share your documents", "upload your documents", "document submission",
    "required documents", "documents for verification", "educational certificates",
)
_REJECTION_PHRASES = (
    "regret to inform", "not been selected", "not selected for",
    "not moving forward", "not shortlisted", "unable to move forward",
    "application was unsuccessful", "application has been unsuccessful",
    "decided not to proceed", "will not be proceeding", "position has been closed",
    "position is closed", "role has been filled", "we have decided to move ahead with other",
    "not a fit at this time", "your profile has not been shortlisted",
    "candidature has been rejected", "we are not proceeding",
)

# ── Provenance ───────────────────────────────────────────────────────────────
#
# Who is speaking matters more than what they say. A job portal writing "your
# profile has been shortlisted for our top client" is running a campaign to
# harvest profile details; a company writing the same words has made a
# decision. These are the phrases that identify the campaign.

_BULK_CAMPAIGN_PHRASES = (
    "while reviewing top talents", "top talents on", "job invite from recruiter",
    "you've been chosen from a large pool", "you have been chosen from a large pool",
    "chosen from a large pool of jobseekers", "you're invited to apply",
    "you are invited to apply", "invited to apply to this job",
    "exciting career opportunities", "top applicant", "urgent vacancy",
    "our top client", "for our top client", "posted by", "job description",
    "apply now", "get app", "not disclosed", "unsubscribe",
    "reviewing top talents on", "in your industry",
)

# Mail whose entire purpose is to collect or confirm the candidate's own
# details. It reads like progress and is not.
_PROFILE_DETAILS_PHRASES = (
    "profile details required", "confirm your profile details",
    "please confirm your profile details", "verify profile details",
    "please verify profile details", "confirm details now",
    "complete pending details", "pending details", "details required",
    "update your profile details", "confirm your details",
    "share your profile details", "we came up short on details",
)

# Boilerplate closing lines. They describe a policy for the general case and
# say nothing about this candidate's application.
_GENERIC_REJECTION_PHRASES = (
    "assume that your profile has not been shortlisted",
    "please assume that your profile", "if you don't hear back from us",
    "if you do not hear back from us", "in case you don't hear back",
    "in case you do not hear back", "unable to respond to every applicant",
    "due to the volume of applications", "we get back to each candidate",
    "get back to each candidate",
)

SOURCE_COMPANY = "COMPANY"
SOURCE_PORTAL = "JOB_PORTAL"
SOURCE_THIRD_PARTY = "THIRD_PARTY_RECRUITER"
SOURCE_PERSONAL = "PERSONAL_MAIL"
SOURCE_UNKNOWN = "UNKNOWN"

STRENGTH_STRONG = "STRONG"
STRENGTH_MODERATE = "MODERATE"
STRENGTH_WEAK = "WEAK"


# Recruiter interest and pipeline noise.  Present in mail that looks positive
# but carries no company decision.
_INTEREST_PHRASES = (
    "we came across your profile", "your profile matches", "are you interested",
    "would you be interested", "let us know your interest", "share your updated resume",
    "share your cv", "confirm your interest", "opportunity with us",
    "we have an opening", "we are hiring", "job opportunity",
)
_NOISE_PHRASES = (
    "job recommendation", "recommended jobs", "jobs matching your profile",
    "new jobs for you", "jobs for you", "similar jobs", "job alert",
    "hiring alert", "featured jobs", "new openings", "daily job alert",
    "weekly job alert", "increase profile visibility", "upgrade your account",
    "premium subscription", "career newsletter", "profile viewed",
    "resume viewed", "searched your profile", "thank you for applying",
    "application received", "application submitted", "we have received your application",
    "your application is under review", "assessment invitation",
    "complete the assessment", "coding test", "verify your email",
    "password reset", "one time password", "otp",
)

_SIGNALS = _compile((
    (JOINING_CONFIRMED, _JOINING_PHRASES),
    (VERIFIED_OFFER_LETTER, _OFFER_LETTER_PHRASES),
    (OFFER_INDICATION, _OFFER_INDICATION_PHRASES),
    (FINAL_SELECTION, _SELECTION_PHRASES),
    (BACKGROUND_VERIFICATION, _BGV_PHRASES),
    (INTERVIEW_CANCELLED, _CANCEL_PHRASES),
    (INTERVIEW_RESCHEDULED, _RESCHEDULE_PHRASES),
    (NEXT_ROUND, _NEXT_ROUND_PHRASES),
    (SHORTLISTED, _SHORTLIST_PHRASES),
    (INTERVIEW_INVITE, _INTERVIEW_PHRASES),
    (REJECTED, _REJECTION_PHRASES),
))
_DOCUMENT_SIGNALS = _compile(((("DOCUMENT_REQUEST"), _DOCUMENT_PHRASES),))
_INTEREST_SIGNALS = _compile((("RECRUITER_INTEREST", _INTEREST_PHRASES),))
_NOISE_SIGNALS = _compile((("NOISE", _NOISE_PHRASES),))
_QUESTIONNAIRE_SIGNALS = _compile((("QUESTIONNAIRE", _QUESTIONNAIRE_PHRASES),))
_BULK_SIGNALS = _compile((("BULK_CAMPAIGN", _BULK_CAMPAIGN_PHRASES),))
_PROFILE_DETAILS_SIGNALS = _compile((("PROFILE_DETAILS_REQUEST", _PROFILE_DETAILS_PHRASES),))
_GENERIC_REJECTION_SIGNALS = _compile((("GENERIC_REJECTION", _GENERIC_REJECTION_PHRASES),))
_OFFER_DOC_CONTENT = _compile((("OFFER_DOC", _OFFER_DOC_CONTENT_PHRASES),))
_NON_OFFER_DOC = _compile((("NON_OFFER_DOC", _NON_OFFER_DOC_PHRASES),))

# Structural detail extractors.  These are what separate "we are pleased to
# offer you" boilerplate from a real offer.
_CTC = re.compile(
    r"(?i)(?:(?:annual|fixed|total|gross|monthly)\s+)?"
    r"(?:ctc|compensation|salary|package|remuneration)\b[^.\n]{0,60}?"
    r"(?:inr|rs\.?|₹|usd|\$)?\s*[\d][\d,\.]{3,}"
    r"|(?:inr|rs\.?|₹)\s*[\d][\d,\.]{3,}\s*(?:lpa|per annum|p\.a\.|lakhs?|/-)?"
    r"|[\d]+(?:\.\d+)?\s*(?:lpa|lakhs? per annum)"
)
_DATE = re.compile(
    r"(?i)\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+\d{2,4})?"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,?\s+\d{2,4})?)\b"
)
_TIME = re.compile(r"(?i)\b\d{1,2}[:.]\d{2}\s*(?:am|pm|hrs|ist|utc)?\b|\b\d{1,2}\s*(?:am|pm)\b")
_JOB_TITLE = re.compile(
    r"(?i)\b(?:position|designation|role|title|job title)\b\s*(?:of|:|-|is)?\s*[a-z][\w /&+.-]{2,60}"
)
_OFFER_DOC_FILENAME = re.compile(
    r"(?i)(offer|appointment|joining|employment|loi|letter[_\- ]?of[_\- ]?intent|compensation|ctc)"
)
_OFFER_DOC_TYPES = frozenset({
    "OFFER_LETTER", "APPOINTMENT_LETTER", "JOINING_LETTER",
    "COMPENSATION_BREAKUP", "EMPLOYMENT_CONTRACT",
})
_FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com",
    "hotmail.com", "live.com", "rediffmail.com", "protonmail.com", "aol.com",
    "icloud.com", "zoho.com", "mail.com", "yandex.com", "gmx.com",
})
# Portals legitimately relay recruiter mail.  Their domain not matching the
# hiring company is normal, not a spoofing signal.
_KNOWN_RELAY_DOMAINS = frozenset({
    "naukri.com", "linkedin.com", "indeed.com", "monster.com", "monsterindia.com",
    "shine.com", "timesjobs.com", "hirist.com", "instahyre.com", "cutshort.io",
    "foundit.in", "glassdoor.com", "wellfound.com", "greenhouse.io",
    "lever.co", "workday.com", "myworkday.com", "smartrecruiters.com",
    "icims.com", "successfactors.com", "taleo.net", "zohorecruit.com",
    "keka.com", "darwinbox.com", "freshteam.com", "ashbyhq.com",
})


def domain_of(address: Any) -> str:
    value = str(address or "").strip().lower()
    if "@" not in value:
        return ""
    domain = value.rsplit("@", 1)[1].strip("<> \t\r\n.")
    return domain


def registrable_domain(domain: str) -> str:
    """Collapse mail subdomains so careers.acme.com matches acme.com."""
    parts = [part for part in str(domain or "").lower().split(".") if part]
    if len(parts) <= 2:
        return ".".join(parts)
    # Two-level public suffixes common in this dataset (co.in, co.uk, com.au).
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# "you have not been selected for the position" contains "selected for the
# position". Without this guard a rejection also reads as a selection, and the
# two together escalate to manual review instead of being reported as the
# rejection it plainly is.
_NEGATOR = re.compile(
    r"(?i)\b(?:not|never|no longer|unable to|cannot|can't|couldn't|won't|unfortunately)\b"
    r"[\w\s,'’-]{0,24}$"
)


def _matches(patterns: list[re.Pattern], text: str, *, negation_aware: bool = False) -> list[str]:
    found = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            if negation_aware and _NEGATOR.search(text[max(0, match.start() - 40):match.start()]):
                continue
            found.append(match.group(0).strip())
            break
    return found


def _excerpt(text: str, phrase: str, width: int = 160) -> str:
    index = text.find(phrase.lower())
    if index < 0:
        return phrase[:width]
    start = max(0, index - width // 3)
    return text[start:start + width].strip()


def attachment_texts(attachments: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize stored or freshly fetched attachment records."""
    result = []
    for item in attachments or []:
        result.append({
            "filename": str(item.get("filename") or ""),
            "mime_type": str(item.get("mime_type") or ""),
            "attachment_type": str(item.get("attachment_type") or "").upper(),
            "extraction_status": str(item.get("extraction_status") or "").upper(),
            "checksum": str(item.get("checksum") or ""),
            "size": item.get("size"),
            "text": str(item.get("text") or item.get("extracted_text") or ""),
        })
    return result


def _offer_document(attachments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The attachment that is plausibly an offer or appointment letter.

    Content decides. A payslip named 29-MAY-2026.pdf can be tagged
    OFFER_LETTER upstream and carries a salary figure, a date and a job title,
    so a candidate whose employer mailed them a payslip was being reported as
    holding a verified offer.
    """
    candidates = [
        item for item in attachments
        if item["attachment_type"] in _OFFER_DOC_TYPES
        or _OFFER_DOC_FILENAME.search(item["filename"])
    ]
    # Prefer one whose extracted text actually reads like an offer letter.
    # Offer language decides; non-offer vocabulary only breaks a weak tie. A
    # real appointment letter routinely mentions relieving letters in its terms
    # and conditions, so treating that vocabulary as a veto rejected genuine
    # offers, while a payslip carries none of the offer language at all.
    for item in candidates:
        text = normalize(item["text"])
        if not text:
            continue
        offer_markers = _matches(_OFFER_DOC_CONTENT[0][1], text)
        non_offer_markers = _matches(_NON_OFFER_DOC[0][1], text)
        if len(offer_markers) >= 2 or (offer_markers and not non_offer_markers):
            return item
    # Otherwise fall back to a named-but-unreadable candidate, so an offer
    # attachment that failed extraction still routes to a human.
    for item in candidates:
        if not normalize(item["text"]):
            return item
    return None


def _detail_signals(text: str) -> dict[str, bool]:
    return {
        "compensation": bool(_CTC.search(text)),
        "date": bool(_DATE.search(text)),
        "job_title": bool(_JOB_TITLE.search(text)),
        "time": bool(_TIME.search(text)),
    }


def classify_source(sender_email: Any, company_domain: Any = None) -> str:
    """Who sent this: the hiring company, a portal, an agency, or a person."""
    root = registrable_domain(domain_of(sender_email))
    if not root:
        return SOURCE_UNKNOWN
    if root in _KNOWN_RELAY_DOMAINS:
        return SOURCE_PORTAL
    if root in _FREE_MAIL_DOMAINS:
        return SOURCE_PERSONAL
    claimed = registrable_domain(str(company_domain or ""))
    if claimed and claimed == root:
        return SOURCE_COMPANY
    if claimed:
        return SOURCE_THIRD_PARTY
    # No company to compare against; a corporate-looking domain is the best
    # available evidence but is not confirmed to be the hiring company.
    return SOURCE_THIRD_PARTY


def evidence_strength(*, source: str, authenticity: str, bulk: bool,
                      outcome: str = "", has_attachment_proof: bool = False) -> str:
    """How much weight this finding can carry toward a status change.

    A portal or agency relaying good news is not the company confirming it, so
    that evidence is reported and never acted on by itself.
    """
    if bulk or source == SOURCE_PORTAL:
        return STRENGTH_WEAK
    if authenticity == AUTHENTICITY_SUSPICIOUS:
        return STRENGTH_WEAK
    if source == SOURCE_PERSONAL:
        return STRENGTH_WEAK
    if source == SOURCE_COMPANY:
        if authenticity == AUTHENTICITY_PASS or has_attachment_proof:
            return STRENGTH_STRONG
        return STRENGTH_MODERATE
    if source == SOURCE_THIRD_PARTY:
        return STRENGTH_MODERATE if has_attachment_proof else STRENGTH_WEAK
    return STRENGTH_WEAK


INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Needs manual review — evidence is insufficient for a status change."
)


def approval_eligibility(finding: dict[str, Any], *,
                         later_conflict: bool = False) -> dict[str, Any]:
    """Whether one finding may be offered as an approvable status change.

    Every condition must hold. Anything short of all of them returns the
    insufficient-evidence message rather than an approve action.
    """
    reasons: list[str] = []
    outcome = str(finding.get("outcome") or "")
    if outcome not in SELECTION_PROOF_OUTCOMES | {SHORTLISTED, NEXT_ROUND, REJECTED}:
        reasons.append("The outcome is not a company decision that maps to a status.")
    if str(finding.get("evidence_strength") or STRENGTH_WEAK) != STRENGTH_STRONG:
        reasons.append("Evidence is not strong enough to act on without verification.")
    if str(finding.get("source_type") or "") != SOURCE_COMPANY:
        reasons.append("The sender is not confirmed to be the hiring company.")
    if str(finding.get("authenticity") or "") == AUTHENTICITY_SUSPICIOUS:
        reasons.append("Sender authenticity is in question.")
    if not str(finding.get("company_name") or finding.get("company_domain") or "").strip():
        reasons.append("The outcome is not tied to an identified company.")
    if not str(finding.get("job_title") or "").strip():
        reasons.append("The outcome is not tied to an identified role.")
    if later_conflict:
        reasons.append("A later message in the same application conflicts with this outcome.")
    if float(finding.get("confidence") or 0) < 80:
        reasons.append("Confidence is below the threshold for an unattended change.")
    return {
        "eligible": not reasons,
        "blockers": reasons,
        "message": "" if not reasons else INSUFFICIENT_EVIDENCE_MESSAGE,
    }


def classify_message(
    message: dict[str, Any],
    attachments: Iterable[dict[str, Any]] | None = None,
    *,
    thread_context: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decide what a single email tells the candidate, from its full content.

    Returns the outcome, a 0-100 confidence, the evidence that produced it, and
    whether a human still needs to look.
    """
    subject_raw = str(message.get("subject") or "")
    body_raw = str(message.get("body") or message.get("body_text") or "")
    html_raw = str(message.get("html_body") or message.get("html_body_text") or "")
    files = attachment_texts(attachments)

    subject = visible_text(subject_raw)
    body = visible_text(body_raw or html_raw)
    attachment_blob = " ".join(item["text"] for item in files)
    thread_blob = " ".join(
        visible_text(f"{item.get('subject') or ''} {item.get('body') or ''}")
        for item in (list(thread_context or [])[-5:])
    )
    # The classified surface is the mail itself.  Thread context informs
    # corroboration but never creates an outcome on its own, otherwise every
    # reply in a thread inherits the original decision.
    primary = f"{subject} {body}".strip()
    full = f"{primary} {normalize(attachment_blob)}".strip()

    evidence: list[dict[str, Any]] = []

    def record(source: str, meaning: str, text: str, phrase: str) -> None:
        evidence.append({
            "source": source, "meaning": meaning,
            "text": _excerpt(text, phrase)[:500] or phrase[:500],
        })

    outbound = str(message.get("message_direction") or "").upper() == "OUTBOUND"
    forwarded = bool(_FORWARD_MARKER.search(subject_raw)) or bool(_FORWARD_MARKER.search(body_raw))

    # Structural facts about the mail.
    has_ics = any(
        "calendar" in item["mime_type"].lower() or item["filename"].lower().endswith(".ics")
        for item in files
    )
    offer_doc = _offer_document(files)
    offer_doc_text = (offer_doc or {}).get("text") or ""
    offer_doc_failed = bool(offer_doc) and (offer_doc or {}).get("extraction_status") in {
        "FAILED", "ERROR", "UNSUPPORTED",
    }
    details_full = _detail_signals(full)
    details_offer_doc = _detail_signals(normalize(offer_doc_text))

    hits: dict[str, list[str]] = {}
    for name, patterns in _SIGNALS:
        # Positive outcomes are read negation-aware; a rejection saying "not
        # selected" must not also register as a selection.
        found = _matches(patterns, full, negation_aware=name != REJECTED)
        if found:
            hits[name] = found

    # Mail the candidate sent is not a company decision.  Without this a
    # candidate writing "I have been selected" would create a selection.
    if outbound:
        if hits:
            record("EMAIL_BODY", "CANDIDATE_AUTHORED", primary, next(iter(hits.values()))[0])
        return _result(
            NOT_RELEVANT, 55, evidence,
            "Message was sent by the candidate, not received from a company.",
            manual_review=False, signals=["OUTBOUND"] + sorted(hits),
        )

    noise = _matches(_NOISE_SIGNALS[0][1], primary)
    interest = _matches(_INTEREST_SIGNALS[0][1], primary)
    documents = _matches(_DOCUMENT_SIGNALS[0][1], full)
    questionnaire = _matches(_QUESTIONNAIRE_SIGNALS[0][1], primary)
    bulk = _matches(_BULK_SIGNALS[0][1], primary)
    details = _matches(_PROFILE_DETAILS_SIGNALS[0][1], primary)
    generic_rejection = _matches(_GENERIC_REJECTION_SIGNALS[0][1], full)

    sender_root = registrable_domain(domain_of(message.get("sender_email")))
    from_portal = sender_root in _KNOWN_RELAY_DOMAINS

    # A job portal or agency blast is a campaign, not a decision. It reaches
    # thousands of candidates with wording that reads like personal progress
    # ("your profile has been shortlisted for our top client"), and treating it
    # as company evidence is how a mailbox of adverts became an offer.
    if from_portal and (len(bulk) >= 2 or details or noise):
        record("EMAIL_BODY", "BULK_CAMPAIGN", primary, (bulk or details or noise)[0])
        return _result(
            NOT_RELEVANT, 65, evidence,
            f"Bulk campaign relayed by the job portal {sender_root}; not a company decision.",
            manual_review=False,
            signals=["BULK_CAMPAIGN", "JOB_PORTAL"] + sorted(hits),
        )
    if len(bulk) >= 3:
        record("EMAIL_BODY", "BULK_CAMPAIGN", primary, bulk[0])
        return _result(
            NOT_RELEVANT, 60, evidence,
            "Mass recruitment campaign or job advertisement; not addressed to this application.",
            manual_review=False, signals=["BULK_CAMPAIGN"] + sorted(hits),
        )

    # A request to supply or confirm the candidate's own details reads like
    # progress and states no company decision, whoever sent it.
    if details and not (hits.keys() & (SELECTION_PROOF_OUTCOMES | {JOINING_CONFIRMED})):
        record("EMAIL_BODY", "PROFILE_DETAILS_REQUEST", primary, details[0])
        return _result(
            NOT_RELEVANT, 55, evidence,
            "Request to supply or confirm profile details; not an interview round "
            "or a hiring decision.",
            manual_review=False,
            signals=["PROFILE_DETAILS_REQUEST"] + sorted(hits),
        )

    # Boilerplate that describes a policy for everyone is not this candidate's
    # rejection. Without a specific application reference it proves nothing.
    if REJECTED in hits and generic_rejection:
        # A rejection phrase that sits inside the boilerplate sentence is the
        # boilerplate; one that stands on its own is a real rejection.
        specific = [
            item for item in hits[REJECTED]
            if not any(item.lower() in phrase.lower() for phrase in generic_rejection)
        ]
        if not specific:
            record("EMAIL_BODY", "GENERIC_REJECTION_BOILERPLATE", full, generic_rejection[0])
            hits.pop(REJECTED)
            if not hits:
                return _result(
                    NOT_RELEVANT, 55, evidence,
                    "Generic 'if you do not hear from us' boilerplate; it does not "
                    "reference a specific application.",
                    manual_review=False, signals=["GENERIC_REJECTION"],
                )

    # A recruiter screening form asks the candidate to declare their own
    # status. It contains "offer letter", "CTC" and a joining date while
    # conveying no company decision at all, so it is settled before any
    # outcome signal is read.
    if len(questionnaire) >= 2 and JOINING_CONFIRMED not in hits:
        record("EMAIL_BODY", "RECRUITER_QUESTIONNAIRE", primary, questionnaire[0])
        return _result(
            NOT_RELEVANT, 55, evidence,
            "Recruiter screening form asking the candidate to state their own "
            "CTC, notice period or offer status; not a company decision.",
            manual_review=False, signals=["QUESTIONNAIRE"] + sorted(hits),
        )

    # Job adverts and portal blasts list document requirements and salary
    # ranges. Treating their wording as an outcome credits a candidate with an
    # offer from a mass mail they were one of thousands to receive.
    if len(noise) >= 2 or (noise and not interest and len(hits) <= 1):
        record("EMAIL_SUBJECT", "JOB_ADVERT_OR_PORTAL", primary, noise[0])
        return _result(
            NOT_RELEVANT, 60, evidence,
            "Job advertisement or portal notification, not addressed decision mail.",
            manual_review=False, signals=["NOISE"] + sorted(hits),
        )

    # ── Nothing meaningful ───────────────────────────────────────────────
    if not hits:
        if documents:
            # A document request on its own is a real recruiter action but not
            # a hiring decision.  It is reported, never promoted.
            record("EMAIL_BODY", "DOCUMENT_REQUEST", full, documents[0])
            return _result(
                NOT_RELEVANT, 40, evidence,
                "Document request only; no hiring decision stated.",
                manual_review=False, signals=["DOCUMENT_REQUEST"],
            )
        if interest:
            record("EMAIL_BODY", "RECRUITER_INTEREST", primary, interest[0])
            return _result(
                NOT_RELEVANT, 45, evidence,
                "Recruiter interest or sourcing outreach; not a company decision.",
                manual_review=False, signals=["RECRUITER_INTEREST"],
            )
        return _result(
            NOT_RELEVANT, 60 if noise else 35, evidence,
            "No outcome language found in subject, body or attachments."
            if not noise else "Job-portal or transactional mail.",
            manual_review=False, signals=["NOISE"] if noise else [],
        )

    # ── Rejection versus positive decision in the same mail ──────────────
    positive_hits = {name for name in hits if name in POSITIVE_DECISION_OUTCOMES}
    if REJECTED in hits and positive_hits:
        record("EMAIL_BODY", "CANDIDATE_REJECTED", full, hits[REJECTED][0])
        for name in sorted(positive_hits):
            record("EMAIL_BODY", name, full, hits[name][0])
        return _result(
            MANUAL_REVIEW_REQUIRED, 50, evidence,
            "The same mail carries both rejection and positive-decision language.",
            manual_review=True, signals=sorted(hits),
        )

    # ── Joining confirmed ────────────────────────────────────────────────
    # An offer letter states a date of joining as one of its terms.  That is
    # part of the offer, not a separate confirmation that the candidate joined,
    # so joining only wins when the mail itself says so.
    # An offer mail always states a joining date, in the body as well as in the
    # attachment. Joining only wins when the mail is itself about joining
    # rather than about the offer that sets the date.
    joining_in_subject = bool(_matches(dict(_SIGNALS)[JOINING_CONFIRMED], subject))
    offer_present = bool(hits.keys() & {VERIFIED_OFFER_LETTER, OFFER_INDICATION})
    if JOINING_CONFIRMED in hits and offer_present and not joining_in_subject:
        hits.pop(JOINING_CONFIRMED)

    if JOINING_CONFIRMED in hits:
        record("EMAIL_BODY", "JOINING_CONFIRMED", full, hits[JOINING_CONFIRMED][0])
        # "date of joining" appears in offer letters and in BGV forms asking the
        # candidate to propose one.  A confirmation states a date.
        if details_full["date"]:
            return _result(
                JOINING_CONFIRMED, 88, evidence,
                "Joining confirmation with a stated date.",
                manual_review=False, signals=sorted(hits),
            )
        return _result(
            MANUAL_REVIEW_REQUIRED, 45, evidence,
            "Joining language without a stated joining date.",
            manual_review=True, signals=sorted(hits),
        )

    # ── Offer letter, verified only with real detail ──────────────────────
    if VERIFIED_OFFER_LETTER in hits:
        record("EMAIL_BODY", "OFFER_LETTER_REFERENCED", full, hits[VERIFIED_OFFER_LETTER][0])
        if offer_doc and offer_doc_text:
            confirmed = sum(1 for key in ("compensation", "date", "job_title") if details_offer_doc[key])
            if confirmed >= 2:
                evidence.append({
                    "source": "ATTACHMENT", "meaning": "OFFER_LETTER_CONTENT",
                    "text": (offer_doc["filename"] + ": " + offer_doc_text.strip()[:400]),
                })
                return _result(
                    VERIFIED_OFFER_LETTER, 92, evidence,
                    f"Offer document {offer_doc['filename']} contains genuine offer details.",
                    manual_review=False, signals=sorted(hits),
                )
            return _result(
                MANUAL_REVIEW_REQUIRED, 50, evidence,
                f"Offer document {offer_doc['filename']} lacks verifiable offer details.",
                manual_review=True, signals=sorted(hits),
            )
        if offer_doc_failed or (offer_doc and not offer_doc_text):
            return _result(
                MANUAL_REVIEW_REQUIRED, 40, evidence,
                f"Offer document {offer_doc['filename']} could not be read; contents unverified.",
                manual_review=True, signals=sorted(hits) + ["ATTACHMENT_UNREADABLE"],
            )
        # No offer document. An offer letter is a document, so without one the
        # mail can at most be an indication that an offer exists — never a
        # verified offer letter. Deriving "verified" from body text alone
        # turned recruiter mail that merely mentions offer letters into offers.
        if OFFER_INDICATION in hits:
            return _result(
                OFFER_INDICATION, 72, evidence,
                "Offer stated in the mail body; no offer document attached to verify.",
                manual_review=False, signals=sorted(hits),
            )
        return _result(
            MANUAL_REVIEW_REQUIRED, 45, evidence,
            "An offer letter is referenced but not attached, and the body states no offer terms.",
            manual_review=True, signals=sorted(hits),
        )

    # ── Offer indication ─────────────────────────────────────────────────
    if OFFER_INDICATION in hits:
        record("EMAIL_BODY", "OFFER_INDICATION", full, hits[OFFER_INDICATION][0])
        return _result(
            OFFER_INDICATION, 78, evidence,
            "Company states intent to offer without a released offer letter.",
            manual_review=False, signals=sorted(hits),
        )

    # ── Final selection ──────────────────────────────────────────────────
    if FINAL_SELECTION in hits:
        record("EMAIL_BODY", "FINAL_SELECTION", full, hits[FINAL_SELECTION][0])
        # Selection language inside an interview invitation ("selected for the
        # technical round") is scheduling, not a hiring decision.
        if INTERVIEW_INVITE in hits and not (details_full["compensation"] or OFFER_INDICATION in hits):
            record("EMAIL_BODY", "INTERVIEW_CONTEXT", full, hits[INTERVIEW_INVITE][0])
            return _result(
                MANUAL_REVIEW_REQUIRED, 48, evidence,
                "Selection wording appears alongside interview scheduling; not a clear final decision.",
                manual_review=True, signals=sorted(hits),
            )
        return _result(
            FINAL_SELECTION, 85, evidence,
            "Company confirms the candidate was selected.",
            manual_review=False, signals=sorted(hits),
        )

    # ── Background verification ──────────────────────────────────────────
    if BACKGROUND_VERIFICATION in hits:
        record("EMAIL_BODY", "BACKGROUND_VERIFICATION", full, hits[BACKGROUND_VERIFICATION][0])
        return _result(
            BACKGROUND_VERIFICATION, 80, evidence,
            "Background or employment verification requested. "
            "This alone is not selection or joining confirmation.",
            manual_review=False, signals=sorted(hits),
        )

    # ── Rejection ────────────────────────────────────────────────────────
    if REJECTED in hits:
        record("EMAIL_BODY", "CANDIDATE_REJECTED", full, hits[REJECTED][0])
        return _result(
            REJECTED, 86, evidence,
            "Company states the candidate is not proceeding.",
            manual_review=False, signals=sorted(hits),
        )

    # ── Interview lifecycle ──────────────────────────────────────────────
    if INTERVIEW_CANCELLED in hits:
        record("EMAIL_BODY", "INTERVIEW_CANCELLED", full, hits[INTERVIEW_CANCELLED][0])
        return _result(
            INTERVIEW_CANCELLED, 84, evidence,
            "Interview cancelled.", manual_review=False, signals=sorted(hits),
        )
    if INTERVIEW_RESCHEDULED in hits:
        record("EMAIL_BODY", "INTERVIEW_RESCHEDULED", full, hits[INTERVIEW_RESCHEDULED][0])
        return _result(
            INTERVIEW_RESCHEDULED, 84 if (details_full["date"] or has_ics) else 60, evidence,
            "Interview rescheduled." if (details_full["date"] or has_ics)
            else "Reschedule stated without a new date.",
            manual_review=not (details_full["date"] or has_ics), signals=sorted(hits),
        )
    if NEXT_ROUND in hits:
        record("EMAIL_BODY", "NEXT_ROUND", full, hits[NEXT_ROUND][0])
        return _result(
            NEXT_ROUND, 80, evidence,
            "Candidate progresses to a further interview round. "
            "A next-round message is not an offer.",
            manual_review=False, signals=sorted(hits),
        )
    if SHORTLISTED in hits:
        record("EMAIL_BODY", "SHORTLISTED", full, hits[SHORTLISTED][0])
        return _result(
            SHORTLISTED, 80, evidence,
            "Candidate shortlisted.", manual_review=False, signals=sorted(hits),
        )
    if INTERVIEW_INVITE in hits:
        record("EMAIL_BODY", "INTERVIEW_INVITE", full, hits[INTERVIEW_INVITE][0])
        if has_ics:
            evidence.append({
                "source": "ATTACHMENT", "meaning": "CALENDAR_INVITE",
                "text": "Calendar invite attached.",
            })
        scheduled = has_ics or (details_full["date"] and details_full["time"])
        return _result(
            INTERVIEW_INVITE, 85 if scheduled else 62, evidence,
            "Interview invitation with schedule details." if scheduled
            else "Interview mentioned without a confirmed date and time.",
            manual_review=not scheduled, signals=sorted(hits),
        )

    return _result(
        NOT_RELEVANT, 30, evidence, "No decisive outcome evidence.",
        manual_review=False, signals=sorted(hits),
    )


def _result(
    outcome: str, confidence: float, evidence: list[dict[str, Any]],
    rationale: str, *, manual_review: bool, signals: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "outcome_rank": OUTCOME_RANK.get(outcome, 0),
        "confidence": round(float(confidence), 2),
        "rationale": rationale,
        "evidence": evidence,
        "manual_review_required": bool(manual_review) or outcome == MANUAL_REVIEW_REQUIRED,
        "signals": signals or [],
    }


# ── Authenticity ─────────────────────────────────────────────────────────────

_AUTH_RESULT = re.compile(r"(?i)\b(spf|dkim|dmarc)\s*=\s*([a-z]+)")


def parse_authentication(header: Any) -> dict[str, str]:
    """Extract spf/dkim/dmarc verdicts from an Authentication-Results header."""
    results: dict[str, str] = {}
    for mechanism, verdict in _AUTH_RESULT.findall(str(header or "")):
        key = mechanism.lower()
        # Keep the first verdict; Gmail lists the authoritative one first.
        results.setdefault(key, verdict.lower())
    return results


def assess_authenticity(
    message: dict[str, Any],
    *,
    company_domain: str | None = None,
    mailbox_email: str | None = None,
    attachments: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Judge how much the sender of an outcome mail can be trusted.

    Reports what was checked and what could not be checked.  Missing headers
    produce UNVERIFIED, never PASS, and never an accusation.
    """
    sender = str(message.get("sender_email") or "").strip().lower()
    sender_domain = domain_of(sender)
    root = registrable_domain(sender_domain)
    auth = parse_authentication(message.get("authentication_results"))
    spf_header = str(message.get("received_spf") or "")
    if "spf" not in auth and spf_header:
        match = re.match(r"\s*([a-z]+)", spf_header.strip(), re.IGNORECASE)
        if match:
            auth["spf"] = match.group(1).lower()

    checks: list[dict[str, Any]] = []
    concerns: list[str] = []
    notes: list[str] = []

    def check(name: str, state: str, detail: str) -> None:
        checks.append({"check": name, "state": state, "detail": detail})

    for mechanism in ("spf", "dkim", "dmarc"):
        verdict = auth.get(mechanism)
        if not verdict:
            check(mechanism.upper(), "UNAVAILABLE", "No result recorded for this message.")
        elif verdict in {"pass"}:
            check(mechanism.upper(), "PASS", f"{mechanism}={verdict}")
        elif verdict in {"neutral", "none", "policy", "unknown", "temperror", "permerror"}:
            check(mechanism.upper(), "INCONCLUSIVE", f"{mechanism}={verdict}")
        else:
            check(mechanism.upper(), "FAIL", f"{mechanism}={verdict}")
            concerns.append(f"{mechanism.upper()} did not pass ({verdict}).")

    # Reply-To / Return-Path. Absent on historical mail; reported as such.
    reply_to = str(message.get("reply_to_email") or "").strip().lower()
    return_path = str(message.get("return_path_email") or "").strip().lower()
    for label, value in (("REPLY_TO", reply_to), ("RETURN_PATH", return_path)):
        if not value:
            check(label, "UNAVAILABLE", "Header not captured for this message.")
            continue
        value_root = registrable_domain(domain_of(value))
        if value_root and root and value_root != root:
            check(label, "MISMATCH", f"{value} does not share the sender domain {root}.")
            concerns.append(f"{label.replace('_', '-').title()} domain {value_root} differs from sender {root}.")
        else:
            check(label, "PASS", value)

    # Sender versus the company the mail claims to represent.
    claimed = registrable_domain(str(company_domain or ""))
    if claimed and root:
        if claimed == root:
            check("COMPANY_DOMAIN", "PASS", f"Sender domain matches {claimed}.")
        elif root in _KNOWN_RELAY_DOMAINS:
            check("COMPANY_DOMAIN", "RELAY", f"Sent through recruiting platform {root}.")
            notes.append(f"Delivered via {root}, a known recruiting platform.")
        elif root in _FREE_MAIL_DOMAINS:
            check("COMPANY_DOMAIN", "MISMATCH", f"Free mail sender {root} for company {claimed}.")
            concerns.append(
                f"Mail about {claimed} was sent from the free-mail domain {root}."
            )
        else:
            check("COMPANY_DOMAIN", "MISMATCH", f"Sender {root} differs from company domain {claimed}.")
            concerns.append(f"Sender domain {root} does not match the stated company domain {claimed}.")
    elif root in _FREE_MAIL_DOMAINS:
        check("COMPANY_DOMAIN", "WEAK", f"Sender uses the free-mail domain {root}.")
        notes.append(f"Sender {root} is a personal mail domain, so the company cannot be confirmed from the domain alone.")
    else:
        check("COMPANY_DOMAIN", "UNAVAILABLE", "No company domain to compare against.")

    # Forwarded mail: the original sender's authentication does not survive.
    subject_raw = str(message.get("subject") or "")
    body_raw = str(message.get("body") or message.get("body_text") or "")
    forwarded = bool(_FORWARD_MARKER.search(subject_raw) or _FORWARD_MARKER.search(body_raw))
    self_sent = bool(mailbox_email) and sender == str(mailbox_email or "").strip().lower()
    if forwarded or self_sent:
        check("FORWARDING", "DETECTED",
              "Message appears forwarded; headers describe the forwarder, not the original sender.")
        notes.append(
            "Forwarded mail: authentication results apply to the forwarding account, "
            "so the original sender is not independently verified."
        )
    else:
        check("FORWARDING", "NOT_DETECTED", "No forwarding markers found.")

    # Attachment shape.
    files = attachment_texts(attachments)
    for item in files:
        mime = item["mime_type"].lower()
        name = item["filename"].lower()
        if not name:
            continue
        executable = name.endswith((".exe", ".scr", ".js", ".vbs", ".jar", ".bat", ".cmd"))
        if executable:
            check("ATTACHMENT_TYPE", "SUSPICIOUS", f"{item['filename']} is an executable attachment.")
            concerns.append(f"Attachment {item['filename']} is an executable file type.")
        elif _OFFER_DOC_FILENAME.search(name) and mime and not any(
            token in mime for token in ("pdf", "word", "officedocument", "msword", "octet-stream", "text")
        ):
            check("ATTACHMENT_TYPE", "MISMATCH",
                  f"{item['filename']} declares an unexpected type {item['mime_type']}.")
            concerns.append(f"Offer document {item['filename']} has an unexpected MIME type {item['mime_type']}.")

    passes = sum(1 for item in checks if item["state"] == "PASS")
    unavailable = sum(1 for item in checks if item["state"] == "UNAVAILABLE")

    if concerns:
        verdict = AUTHENTICITY_SUSPICIOUS
    elif auth.get("spf") == "pass" and auth.get("dkim") == "pass" and not forwarded and not self_sent:
        verdict = AUTHENTICITY_PASS if passes >= 3 else AUTHENTICITY_PARTIAL
    elif passes and unavailable < len(checks):
        verdict = AUTHENTICITY_PARTIAL
    else:
        verdict = AUTHENTICITY_UNVERIFIED

    return {
        "verdict": verdict,
        "sender_email": sender,
        "sender_domain": sender_domain,
        "sender_root_domain": root,
        "company_domain": claimed or None,
        "authentication": auth,
        "checks": checks,
        "concerns": concerns,
        "notes": notes,
        "forwarded": forwarded or self_sent,
    }


# ── Aggregation ──────────────────────────────────────────────────────────────

def strongest(findings: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the furthest-progressed outcome, preferring higher confidence."""
    best = None
    for finding in findings:
        outcome = str(finding.get("outcome") or NOT_RELEVANT)
        if outcome not in MEANINGFUL_OUTCOMES:
            continue
        key = (
            OUTCOME_RANK.get(outcome, 0),
            float(finding.get("confidence") or 0),
            str(finding.get("received_at") or ""),
        )
        if best is None or key > best[0]:
            best = (key, finding)
    return best[1] if best else None


def detect_conflicts(findings: Iterable[dict[str, Any]]) -> list[str]:
    """Report contradictions a human must resolve, per company."""
    by_company: dict[str, set[str]] = {}
    for finding in findings:
        outcome = str(finding.get("outcome") or "")
        if outcome not in MEANINGFUL_OUTCOMES:
            continue
        company = registrable_domain(str(finding.get("company_domain") or "")) or \
            str(finding.get("company_name") or "").strip().lower() or "unknown"
        by_company.setdefault(company, set()).add(outcome)

    conflicts = []
    for company, outcomes in sorted(by_company.items()):
        positive = outcomes & POSITIVE_DECISION_OUTCOMES
        if REJECTED in outcomes and positive:
            conflicts.append(
                f"{company}: rejection recorded alongside {', '.join(sorted(positive))}."
            )
    return conflicts


def outcome_counts(findings: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        outcome = str(finding.get("outcome") or NOT_RELEVANT)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def content_signature(message: dict[str, Any], attachments: Iterable[dict[str, Any]] | None = None) -> str:
    """Stable signature for duplicate detection across message ids.

    Subject plus normalized body plus attachment checksums.  Two Gmail messages
    with different ids but identical content collapse onto one signature, which
    is how a forwarded copy of an offer stops counting twice.
    """
    import hashlib

    subject = visible_text(message.get("subject"))
    body = visible_text(message.get("body") or message.get("body_text") or "")
    checksums = sorted(
        str(item.get("checksum") or "") for item in (attachments or []) if item.get("checksum")
    )
    payload = "|".join([subject, body[:4000], *checksums])
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def calendar_uid(attachments: Iterable[dict[str, Any]] | None) -> str | None:
    """Return the UID of an attached calendar invite, for idempotency."""
    try:
        from services.calendar_invite_parser import parse_calendar
    except Exception:
        return None
    for item in attachment_texts(attachments):
        is_ics = "calendar" in item["mime_type"].lower() or item["filename"].lower().endswith(".ics")
        if not is_ics or not item["text"]:
            continue
        try:
            parsed = parse_calendar(item["text"])
        except Exception:
            continue
        if parsed and parsed.get("uid"):
            return str(parsed["uid"])
    return None


def attachment_fingerprint(attachments: Iterable[dict[str, Any]] | None) -> str | None:
    checksums = sorted(
        str(item.get("checksum") or "") for item in (attachments or []) if item.get("checksum")
    )
    return ",".join(checksums)[:500] or None


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
