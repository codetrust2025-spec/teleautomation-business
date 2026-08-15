"""Service-first allocation of verified money, and what it earns."""
import pytest

from features import candidate_store as cs
from features import payment_allocation as alloc


def test_without_bgv_every_rupee_is_service_money():
    out = alloc.allocate(verified_total=6000, service_expected=5000)
    assert out["service_received"] == 6000
    assert out["bgv_received"] == 0
    assert out["unallocated_excess"] == 0
    assert out["service_outstanding"] == 0


def test_without_bgv_an_overpayment_stays_commissionable():
    """Existing behaviour: expected is a floor, not a cap."""
    out = alloc.allocate(verified_total=12000, service_expected=5000)
    assert alloc.commissionable_amount(out) == 12000


def test_sakthivek_thirty_thousand_settles_service_first():
    out = alloc.allocate(verified_total=30000, service_expected=20000,
                         bgv_expected=30000, bgv_enabled=True)
    assert out["service_received"] == 20000
    assert out["service_outstanding"] == 0
    assert out["bgv_received"] == 10000
    assert out["bgv_outstanding"] == 20000
    assert out["unallocated_excess"] == 0
    assert alloc.commissionable_amount(out) == 20000


def test_a_short_payment_stays_service_money():
    out = alloc.allocate(verified_total=12000, service_expected=20000,
                         bgv_expected=30000, bgv_enabled=True)
    assert out["service_received"] == 12000
    assert out["service_outstanding"] == 8000
    assert out["bgv_received"] == 0
    assert out["bgv_outstanding"] == 30000


def test_bgv_allocation_is_capped_by_the_bgv_expectation():
    out = alloc.allocate(verified_total=50000, service_expected=20000,
                         bgv_expected=30000, bgv_enabled=True)
    assert out["service_received"] == 20000
    assert out["bgv_received"] == 30000
    assert out["unallocated_excess"] == 0


def test_excess_beyond_both_obligations_needs_review():
    out = alloc.allocate(verified_total=60000, service_expected=20000,
                         bgv_expected=30000, bgv_enabled=True)
    assert out["service_received"] == 20000
    assert out["bgv_received"] == 30000
    assert out["unallocated_excess"] == 10000
    assert out["needs_excess_review"] is True
    assert out["excess_state"] == alloc.UNALLOCATED_EXCESS_REVIEW


def test_nothing_paid_allocates_nothing():
    out = alloc.allocate(verified_total=0, service_expected=20000,
                         bgv_expected=30000, bgv_enabled=True)
    assert out["service_received"] == 0
    assert out["bgv_received"] == 0
    assert alloc.commissionable_amount(out) == 0


def test_allocation_is_idempotent():
    kwargs = dict(verified_total=30000, service_expected=20000,
                  bgv_expected=30000, bgv_enabled=True)
    assert alloc.allocate(**kwargs) == alloc.allocate(**kwargs)


# -- through the candidate row ----------------------------------------------

def bgv_row(payment):
    return {"id": "c1", "name": "sakthivek", "reference": "Thrilok",
            "service_type": "profile_service", "expected_payment": 50000,
            "payment": payment, "bgv_certificates": True}


def test_sakthivek_row_splits_and_earns_correctly():
    row = cs._with_computed(bgv_row(30000))
    assert row["payment"] == 30000
    assert row["expected_minimum"] == 50000
    assert row["service_expected"] == 20000
    assert row["service_received"] == 20000
    assert row["service_outstanding"] == 0
    assert row["bgv_expected"] == 30000
    assert row["bgv_received"] == 10000
    assert row["bgv_outstanding"] == 20000
    assert row["balance_due"] == 20000
    assert row["referral_commission"] == 10000
    assert row["company_revenue"] == 10000, "BGV money is never company revenue"


def test_bgv_contributes_nothing_to_earnings():
    row = cs._with_computed(bgv_row(30000))
    assert cs.handler_earning_allocations(row) == {"thrilok": 10000}
    # The extra Rs 10,000 sitting against BGV earns nobody anything.
    assert row["total_handler_earnings"] == 10000


def test_a_bgv_row_paid_in_full_still_only_commissions_the_service():
    row = cs._with_computed(bgv_row(50000))
    assert row["service_received"] == 20000
    assert row["bgv_received"] == 30000
    assert row["referral_commission"] == 10000
    assert row["company_revenue"] == 10000


def test_a_non_bgv_row_is_unaffected_by_the_allocation_engine():
    row = cs._with_computed({
        "id": "c2", "name": "alluraiah", "reference": "Pavan Kalyan",
        "service_type": "round_wise", "interview_scope": "external",
        "expected_payment": 5000, "payment": 6000, "bgv_certificates": False})
    assert row["service_received"] == 6000
    assert row["bgv_received"] == 0
    assert row["referral_commission"] == 3000
    assert row["company_revenue"] == 3000


@pytest.mark.parametrize("payment", [0, 5000, 20000, 30000, 50000])
def test_repeated_computation_never_drifts(payment):
    first = cs._with_computed(bgv_row(payment))
    second = cs._with_computed(bgv_row(payment))
    for field in ("service_received", "bgv_received", "referral_commission",
                  "company_revenue", "balance_due"):
        assert first[field] == second[field]
