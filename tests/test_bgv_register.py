"""BGV cases: balances, isolation from company money, and one case per profile."""
import pytest

from features import bgv_register as bgv
from features import candidate_store as cs


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BGV_REGISTER_FILE", str(tmp_path / "bgv.json"))
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)


def sakthivek_case():
    return bgv.upsert_case(
        candidate_id="d6a32e4b50", candidate_name="sakthivek",
        phone="8220602935", bgv_expected=30000,
        consultancy="BGV vendor", service_description="Background verification",
    )


# -- balances ----------------------------------------------------------------

def test_a_new_case_is_awaiting_collection():
    case = sakthivek_case()
    assert case["bgv_expected"] == 30000
    assert case["bgv_collected"] == 0
    assert case["bgv_outstanding"] == 30000
    assert case["consultancy_payable"] == 0
    assert case["status"] == bgv.AWAITING_COLLECTION


def test_a_partial_collection_leaves_the_balance_outstanding():
    case = sakthivek_case()
    case = bgv.record_collection(
        case_id=case["case_id"], amount=10000, verified=True,
        transaction_reference="250859628039", payment_id="pay_ecd1d4f6637c4c8c")
    assert case["bgv_collected"] == 10000
    assert case["bgv_outstanding"] == 20000
    assert case["status"] == bgv.PARTIALLY_COLLECTED


def test_collecting_the_full_amount_moves_to_payment_pending():
    case = sakthivek_case()
    case = bgv.record_collection(case_id=case["case_id"], amount=30000,
                                 verified=True, transaction_reference="U1")
    assert case["bgv_outstanding"] == 0
    assert case["status"] == bgv.PAYMENT_PENDING
    assert case["consultancy_payable"] == 30000


def test_settlements_reduce_the_payable_balance():
    case = sakthivek_case()
    case = bgv.record_collection(case_id=case["case_id"], amount=30000,
                                 verified=True, transaction_reference="U1")
    case = bgv.record_settlement(case_id=case["case_id"], amount=12000,
                                 verified=True, transaction_reference="S1")
    assert case["paid_to_consultancy"] == 12000
    assert case["consultancy_payable"] == 18000
    assert case["status"] == bgv.PARTIALLY_SETTLED

    case = bgv.record_settlement(case_id=case["case_id"], amount=18000,
                                 verified=True, transaction_reference="S2")
    assert case["consultancy_payable"] == 0
    assert case["status"] == bgv.SETTLED


def test_only_verified_money_counts():
    case = sakthivek_case()
    case = bgv.record_collection(case_id=case["case_id"], amount=10000,
                                 verified=False, transaction_reference="U1")
    assert case["bgv_collected"] == 0, "unverified collection is not money yet"
    case = bgv.record_settlement(case_id=case["case_id"], amount=5000,
                                 verified=False, transaction_reference="S1")
    assert case["paid_to_consultancy"] == 0


def test_balances_never_go_negative_and_over_settlement_is_flagged():
    case = sakthivek_case()
    case = bgv.record_collection(case_id=case["case_id"], amount=10000,
                                 verified=True, transaction_reference="U1")
    case = bgv.record_settlement(case_id=case["case_id"], amount=15000,
                                 verified=True, transaction_reference="S1")
    assert case["consultancy_payable"] == 0, "no negative payable"
    assert case["over_settled"] == 5000
    assert case["needs_adjustment"] is True


# -- financial isolation -----------------------------------------------------

def test_a_case_earns_nobody_anything():
    case = sakthivek_case()
    case = bgv.record_collection(case_id=case["case_id"], amount=30000,
                                 verified=True, transaction_reference="U1")
    assert case["company_earning"] == 0
    assert case["referral_earning"] == 0
    assert case["handler_earning"] == 0


def test_the_dashboard_restates_that_bgv_earns_nothing():
    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=10000, verified=True,
                          transaction_reference="U1")
    board = bgv.dashboard()
    assert board["company_earning_total"] == 0
    assert board["referral_earning_total"] == 0
    assert board["collected_total"] == 10000
    assert board["outstanding_total"] == 20000


def test_a_settlement_creates_no_candidate_or_company_record():
    """The money was never the company's, so paying it onward is not an expense."""
    before = cs._load()
    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=30000, verified=True,
                          transaction_reference="U1")
    bgv.record_settlement(case_id=case["case_id"], amount=30000, verified=True,
                          transaction_reference="S1")
    assert cs._load() == before, "BGV activity must not touch candidate records"


def test_recording_bgv_activity_does_not_change_candidate_earnings():
    row = cs.create_candidate({"name": "sakthivek", "phone": "8220602935",
                               "reference": "Thrilok", "expected_payment": 50000,
                               "payment": 30000})
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == row["id"]:
            item["payment"] = 30000
            item["bgv_certificates"] = True
    cs._save(data)
    before = cs.get_candidate(row["id"])

    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=10000, verified=True,
                          transaction_reference="250859628039")

    after = cs.get_candidate(row["id"])
    assert after["referral_commission"] == before["referral_commission"] == 10000
    assert after["company_revenue"] == before["company_revenue"] == 10000
    assert after["payment"] == 30000


# -- one case per profile ----------------------------------------------------

def test_clone_rows_resolve_to_one_case():
    first = sakthivek_case()
    second = bgv.upsert_case(candidate_id="354347c226", candidate_name="sakthivek",
                             phone="8220602935", bgv_expected=30000)
    assert second["case_id"] == first["case_id"]
    assert len(bgv.list_cases()) == 1


def test_upsert_updates_rather_than_forking_a_case():
    case = sakthivek_case()
    updated = bgv.upsert_case(candidate_id="d6a32e4b50", candidate_name="sakthivek",
                              phone="8220602935", bgv_expected=35000,
                              consultancy="New vendor")
    assert updated["case_id"] == case["case_id"]
    assert updated["bgv_expected"] == 35000
    assert updated["consultancy"] == "New vendor"
    assert any(e["action"] == "case_updated" for e in updated["audit"])


def test_a_repeated_transaction_is_collected_once():
    case = sakthivek_case()
    for _ in range(3):
        case = bgv.record_collection(case_id=case["case_id"], amount=10000,
                                     verified=True,
                                     transaction_reference="250859628039")
    assert case["bgv_collected"] == 10000
    assert len(case["collections"]) == 1


def test_a_repeated_settlement_is_recorded_once():
    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=30000, verified=True,
                          transaction_reference="U1")
    for _ in range(3):
        case = bgv.record_settlement(case_id=case["case_id"], amount=10000,
                                     verified=True, transaction_reference="S1")
    assert case["paid_to_consultancy"] == 10000
    assert len(case["settlements"]) == 1


def test_case_can_be_found_by_profile():
    created = sakthivek_case()
    found = bgv.case_for_profile("sakthivek", "8220602935")
    assert found["case_id"] == created["case_id"]


# -- status and audit --------------------------------------------------------

def test_a_manual_status_overrides_the_derived_one():
    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=10000, verified=True,
                          transaction_reference="U1")
    case = bgv.set_status(case_id=case["case_id"], status=bgv.SENT_TO_CONSULTANCY,
                          actor="administrator", reason="documents dispatched")
    assert case["status"] == bgv.SENT_TO_CONSULTANCY
    assert case["reviewed_by"] == "administrator"


def test_an_unknown_status_is_refused():
    case = sakthivek_case()
    with pytest.raises(ValueError, match="Unknown BGV status"):
        bgv.set_status(case_id=case["case_id"], status="INVENTED", actor="a")


def test_every_change_is_audited():
    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=10000, verified=True,
                          transaction_reference="U1")
    bgv.record_settlement(case_id=case["case_id"], amount=4000, verified=True,
                          transaction_reference="S1")
    case = bgv.set_status(case_id=case["case_id"], status=bgv.IN_PROGRESS,
                          actor="administrator")
    actions = [e["action"] for e in case["audit"]]
    assert actions == ["case_created", "collection_recorded",
                       "settlement_recorded", "status_changed"]
    assert all(e["at"] and e["actor"] for e in case["audit"])


def test_recording_against_an_unknown_case_is_refused():
    with pytest.raises(ValueError, match="No BGV case"):
        bgv.record_collection(case_id="bgv_missing", amount=1, verified=True)


# -- sakthivek's required end state ------------------------------------------

def test_sakthivek_case_matches_the_confirmed_figures():
    case = sakthivek_case()
    case = bgv.record_collection(
        case_id=case["case_id"], amount=10000, verified=True,
        transaction_reference="250859628039",
        transaction_id="T2606221827542453052641",
        payment_id="pay_ecd1d4f6637c4c8c", occurred_on="2026-06-22",
        note="BGV share of the Rs 30,000 canonical payment "
             "(Rs 20,000 service, Rs 10,000 BGV).")
    assert case["bgv_expected"] == 30000
    assert case["bgv_collected"] == 10000
    assert case["bgv_outstanding"] == 20000
    assert case["consultancy_payable"] == 10000
    assert case["company_earning"] == 0
    assert case["referral_earning"] == 0
    assert len(case["collections"]) == 1, "one transfer, one collection entry"


def test_csv_export_carries_the_balances():
    case = sakthivek_case()
    bgv.record_collection(case_id=case["case_id"], amount=10000, verified=True,
                          transaction_reference="250859628039")
    text = bgv.csv_rows(bgv.list_cases())
    header = text.splitlines()[0]
    for column in ("bgv_expected", "bgv_collected", "bgv_outstanding",
                   "consultancy_payable", "company_earning"):
        assert column in header
    assert "sakthivek" in text
    assert "250859628039" in text
