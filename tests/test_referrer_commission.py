from features.candidate_store import (
    admin_complimentary_amount,
    handler_earning_allocations,
    referrer_complimentary_amount,
    referrer_commission_amount,
    referrer_commission_basis,
)


def test_below_tariff_agreed_payment_keeps_full_referral_percentage():
    krishna = {
        "name": "Krishna",
        "reference": "Thrilok",
        "service_type": "round_wise",
        "interview_scope": "internal",
        "expected_payment": 8_000,
        "payment": 8_000,
    }

    assert referrer_commission_basis(krishna) == 8_000
    assert referrer_commission_amount(krishna) == 4_000


def test_partial_payment_earns_half_of_cash_received():
    row = {
        "service_type": "round_wise",
        "interview_scope": "internal",
        "expected_payment": 9_000,
        "payment": 5_000,
    }

    assert referrer_commission_basis(row) == 5_000
    assert referrer_commission_amount(row) == 2_500


def test_payment_above_the_agreed_charge_is_commissionable():
    """Superseded the old cap-at-agreed-charge rule on 2026-08-06.

    The basis used to be `min(received, agreed)`, so a candidate paying ₹8,000
    against a ₹5,000 minimum earned their referrer ₹2,500. Expected is a floor,
    not a ceiling: everything received is revenue the referrer brought in, so
    commission is 50% of the whole amount.
    """
    row = {
        "service_type": "round_wise",
        "interview_scope": "external",
        "expected_payment": 5_000,
        "payment": 8_000,
    }

    assert referrer_commission_basis(row) == 8_000
    assert referrer_commission_amount(row) == 4_000


def test_bgv_pass_through_remains_non_commissionable():
    row = {
        "service_type": "profile_service",
        "bgv_certificates": True,
        "expected_payment": 50_000,
        "payment": 50_000,
    }

    assert referrer_commission_basis(row) == 20_000
    assert referrer_commission_amount(row) == 10_000


def test_completed_profile_adds_both_complimentary_amounts_to_base_commission():
    row = {
        "reference": "Charan",
        "stage": "completed",
        "service_type": "profile_service",
        "expected_payment": 20_000,
        "payment": 20_000,
    }

    assert referrer_commission_amount(row) == 10_000
    assert referrer_complimentary_amount(row) == 5_000
    assert admin_complimentary_amount(row) == 5_000
    assert handler_earning_allocations(row) == {
        "charan": 15_000,
        "thrilok": 5_000,
    }


def test_thrilok_receives_both_extras_when_he_is_the_referrer():
    row = {
        "reference": "Thrilok",
        "stage": "completed",
        "service_type": "profile_service",
        "expected_payment": 20_000,
        "payment": 20_000,
    }

    assert handler_earning_allocations(row) == {"thrilok": 20_000}


def test_complimentary_amounts_require_completed_profile_service():
    incomplete_profile = {
        "reference": "Charan",
        "stage": "in_progress",
        "service_type": "profile_service",
        "payment": 20_000,
    }
    completed_round = {
        "reference": "Charan",
        "stage": "completed",
        "service_type": "round_wise",
        "payment": 8_000,
        "expected_payment": 8_000,
    }

    assert referrer_complimentary_amount(incomplete_profile) == 0
    assert admin_complimentary_amount(incomplete_profile) == 0
    assert referrer_complimentary_amount(completed_round) == 0
    assert admin_complimentary_amount(completed_round) == 0
