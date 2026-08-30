"""Evidence validation must drop invented items, not the whole classification.

Production message 4dbf2094 ("Intent Offer Letter - Infoshare"): both Ollama
passes returned OFFER_IN_PROGRESS at 95% with offer_detected true and a
verbatim body quote — "We are delighted to offer you the position of Senior
Software Engineer…". validate_result raised "selection/offer evidence is
missing or unsupported" and sent it to a permanent retry loop, because a
second, paraphrased evidence item failed the verbatim test and the check
required *all* items to pass.
"""

from services.recruitment_mail_agent import _evidence_supported, _source_texts

SUBJECT = "Intent Offer Letter - Infoshare"
BODY = ("We are delighted to offer you the position of Senior Software Engineer "
        "at our Hyderabad location with discussed CTC. We kindly request you to "
        "visit our office on 17th Aug 2026 at 11:00AM IST.")


def _sources():
    return _source_texts(SUBJECT, BODY, None, None)


def test_a_verbatim_body_quote_is_supported():
    assert _evidence_supported(
        {"source": "EMAIL_BODY",
         "text": "We are delighted to offer you the position of Senior Software Engineer"},
        _sources()) is True


def test_a_verbatim_subject_quote_is_supported():
    assert _evidence_supported(
        {"source": "EMAIL_SUBJECT", "text": SUBJECT}, _sources()) is True


def test_a_paraphrase_is_not_supported():
    """Descriptive summaries are not evidence and must not pass."""
    assert _evidence_supported(
        {"source": "EMAIL_BODY", "text": "Offer letter content and interview schedule"},
        _sources()) is False


def test_hallucinated_text_absent_from_source_is_not_supported():
    assert _evidence_supported(
        {"source": "EMAIL_BODY", "text": "annual CTC of 24,00,000 confirmed in writing"},
        _sources()) is False


def test_a_disclaimer_not_present_in_the_source_is_not_supported():
    assert _evidence_supported(
        {"source": "EMAIL_BODY",
         "text": "unless there is a formal offer this shall not be assumed"},
        _sources()) is False


def test_empty_evidence_text_is_not_supported():
    assert _evidence_supported({"source": "EMAIL_BODY", "text": ""}, _sources()) is False


def test_the_wrong_source_bucket_is_not_supported():
    """A body quote attributed to the subject must not validate."""
    assert _evidence_supported(
        {"source": "EMAIL_SUBJECT",
         "text": "We are delighted to offer you the position"}, _sources()) is False


def test_validate_result_keeps_supported_and_drops_the_rest():
    from services import recruitment_mail_agent as agent
    from tests.test_recruitment_pipeline import structured

    row = structured(
        "OFFER_INDICATION", .95,
        "We are delighted to offer you the position of Senior Software Engineer",
    )
    row["evidence"] = [
        {**row["evidence"][0], "source": "EMAIL_BODY"},
        {"source": "EMAIL_BODY", "meaning": "OFFER_INDICATION", "text": "invented offer details"},
    ]
    agent.validate_result(row, {"subject": SUBJECT, "body": BODY})

    assert row["status"] == "OFFER_INDICATION"
    assert row["backend_transition_validated"] is True
    assert len(row["evidence"]) == 1
    assert row["evidence"][0]["text"].startswith("We are delighted")
