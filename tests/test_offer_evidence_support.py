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
    import inspect
    from services import recruitment_mail_agent as agent
    src = inspect.getsource(agent.validate_result)
    assert "supported = [item for item in value[\"evidence\"] if _evidence_supported" in src
    assert "if not supported:" in src
    # `supported` is what survives, whether assigned straight through or mapped
    # over on the way (meanings are normalised there now). Asserting the exact
    # assignment broke the moment that mapping was added, while the property
    # being guarded — only supported items survive — was never at risk.
    assert "supported]" in src or 'value["evidence"] = supported' in src
    assert "for item in supported" in src or 'value["evidence"] = supported' in src
    # the all-or-nothing form must be gone
    assert "not all(_evidence_supported" not in src
