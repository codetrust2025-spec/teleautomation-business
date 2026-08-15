"""Amount extraction: literal text wins, model arithmetic is never trusted."""
import pytest

from features.ollama_payment_extract import _normalize_amount_number
from features import payment_verification_engine as pve


@pytest.mark.parametrize(("text", "rupees"), [
    ("\u20b930,000", 30000),
    ("\u20b920,000", 20000),
    ("\u20b940,000", 40000),
    ("\u20b910,000", 10000),
    ("\u20b96,000", 6000),
    ("6000", 6000),
    ("6000.00", 6000),
    ("\u20b91,20,000", 120000),      # Indian grouping
    ("INR 1,00,000", 100000),
    ("Rs. 2,50,000", 250000),
    ("  \u20b9 30,000  ", 30000),
])
def test_literal_amount_text_parses_exactly(text, rupees):
    assert _normalize_amount_number(text) == rupees


def normalize(**changes):
    value = {"direction": "PAID_TO", "payment_status": "SUCCESS"}
    value.update(changes)
    return pve._normalize_directional_extraction(value)


def test_literal_text_beats_a_wrong_model_conversion():
    """The production defect: the model returned 300000 'paise' for a ₹30,000
    receipt. The printed text is authoritative."""
    result = normalize(amount_text="\u20b930,000", amount_minor=300000)
    assert result["amount"] == 30000
    assert result["amount_source"] == "literal_text"
    assert not result.get("amount_extraction_review_required")


def test_model_rupees_are_used_when_no_literal_text():
    result = normalize(amount=30000)
    assert result["amount"] == 30000
    assert result["amount_source"] == "model_rupees"


def test_bare_model_minor_units_are_untrusted_and_need_review():
    result = normalize(amount_minor=300000)
    assert result["amount"] == 3000
    assert result["amount_source"] == "model_minor_units_untrusted"
    assert result["amount_extraction_review_required"] is True
    assert result["amount_review_reason"]


def test_factor_of_ten_against_a_visible_amount_requires_review():
    result = normalize(amount=3000, visible_amounts=["\u20b93,000", "\u20b930,000"])
    assert result["amount_extraction_review_required"] is True
    assert "ten times" in result["amount_review_reason"]


def test_two_matching_visible_amounts_corroborate():
    """The sakthivek receipt prints ₹30,000 under both 'Paid to' and
    'Debited from'."""
    result = normalize(
        amount_text="\u20b930,000",
        visible_amounts=["\u20b930,000", "\u20b930,000"],
    )
    assert result["amount"] == 30000
    assert result["amount_corroborated"] is True
    assert not result.get("amount_extraction_review_required")


def test_disagreeing_regions_require_review():
    result = normalize(amount_text="\u20b930,000",
                       visible_amounts=["\u20b930,000", "\u20b912,500"])
    assert result["amount_extraction_review_required"] is True
    assert "disagree" in result["amount_review_reason"]


def test_amount_candidates_are_recorded_for_the_reviewer():
    result = normalize(amount_text="\u20b930,000",
                       visible_amounts=["\u20b930,000", "\u20b912,500"])
    assert result["amount_candidates"] == [12500, 30000]


def test_missing_amount_is_not_invented():
    result = normalize()
    assert result["amount"] == 0
    assert result["amount_source"] == "missing"
    assert not result.get("amount_extraction_review_required")


def test_review_flag_blocks_a_verified_state():
    """Confidence cannot rescue it — every known factor-of-ten error reported
    confidence 1.0."""
    assert "AMOUNT_EXTRACTION_REVIEW_REQUIRED" in pve.VERIFICATION_STATES
    result = normalize(amount_minor=300000)
    assert result["amount_extraction_review_required"] is True
