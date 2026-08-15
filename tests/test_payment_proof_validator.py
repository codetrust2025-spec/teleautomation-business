from features.payment_proof_validator import _extract_max_amount


def test_extract_amount_prefers_payment_over_masked_account_and_ad_price():
    ocr_text = """Payment Successful
21 May 2026 at 10:03 AM
SAMPLE REFERRER
******5810
~10,000 Split Expense
Get Powered Eyeglasses with Blu Lens at $999
"""

    assert _extract_max_amount(ocr_text) == 10000


def test_extract_unformatted_amount_does_not_truncate_masked_account():
    ocr_text = "Payment Successful\n******5810\nPaid Rs 10000"

    assert _extract_max_amount(ocr_text) == 10000
