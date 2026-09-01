"""Regression tests for handler opening/closing balances.

Written after the 2026-08 service split published August opening balances
overstated by Rs 1,32,000. Nothing raised: `handler_expenses.json` and
`handler_salaries.json` were never migrated, the payment ledger arrived with no
entries, and each of the three lookups sat inside its own `except: pass`. Three
missing subtrahends became three silent zeros, so the balance still added up and
still looked authoritative.

Two things are covered here. First, that each component of a balance is applied
exactly once — carry-forward, salary, commission, complimentary, payouts and
recoveries. Second, and more importantly, that an unreadable accounting store
now makes the result say so instead of quietly reporting a confident figure.
"""
from __future__ import annotations

import json

import pytest

from features import candidate_store as cs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _candidate(cid: str, reference: str, month: str, payment: int,
               service_type: str = "round_wise") -> dict:
    """One completed candidate row in the shape the store returns.

    Commission follows money actually received, so `payment` is what drives the
    handler's 50% share. `round_wise` is the default because a closed
    `profile_service` row additionally earns two Rs 5,000 complimentary
    amounts, which would obscure the arithmetic every test but one is checking.
    """
    return {
        "id": cid,
        # Profile-service rows are collapsed one-per-client by name, so a row
        # without one is dropped before it can earn anything.
        "name": f"Candidate {cid}",
        "reference": reference,
        "date": f"{month}-15",
        "stage": "completed",
        "payment": payment,
        "expected_payment": payment,
        "service_type": service_type,
    }


@pytest.fixture()
def accounting(tmp_path, monkeypatch):
    """All four accounting sources on scratch files, each independently controllable.

    Returns a helper whose `write_*` methods seed a source and whose
    `delete_*` methods remove one, so a test can reproduce the exact
    post-cutover condition: present-but-empty versus genuinely absent.
    """
    from features import handler_expenses, handler_salaries
    from features import payment_verification_engine as pve

    expenses_file = tmp_path / "handler_expenses.json"
    salaries_file = tmp_path / "handler_salaries.json"
    ledger_file = tmp_path / "payment_verification_ledger.json"

    monkeypatch.setattr(handler_expenses, "_FILE", str(expenses_file))
    monkeypatch.setattr(handler_expenses, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(handler_salaries, "_FILE", str(salaries_file))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(ledger_file))

    class Sources:
        expenses = expenses_file
        salaries = salaries_file
        ledger = ledger_file

        def write_candidates(self, rows):
            monkeypatch.setattr(cs, "_load", lambda **_: {"candidates": list(rows)})

        def write_expenses(self, rows):
            expenses_file.write_text(json.dumps({"expenses": list(rows)}), encoding="utf-8")

        def write_salaries(self, salaries):
            salaries_file.write_text(json.dumps({"salaries": salaries}), encoding="utf-8")

        def write_ledger(self, entries):
            ledger_file.write_text(
                json.dumps({"schema_version": 2, "entries": list(entries),
                            "payments": [], "evidence": [], "entitlements": []}),
                encoding="utf-8",
            )

        def write_aliases(self, aliases):
            """Seed the referrer alias map that resolves display names to keys."""
            monkeypatch.setattr(cs, "_reference_alias_map", lambda: dict(aliases))

    sources = Sources()
    # Start from a fully readable, fully empty world so every test states its
    # own inputs and an absent file is always deliberate.
    sources.write_candidates([])
    sources.write_expenses([])
    sources.write_salaries({})
    sources.write_ledger([])
    return sources


# ---------------------------------------------------------------------------
# 1-7: every component applies exactly once
# ---------------------------------------------------------------------------

def test_previous_unpaid_balance_carries_forward_exactly_once(accounting):
    """June commission appears in July's opening once, and in August's once."""
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])

    june_commission = cs._carry_forward_balances("2026-07")["thrilok"]["prior_commission"]
    assert june_commission > 0

    august = cs._carry_forward_balances("2026-08")["thrilok"]
    # Nothing happened in July, so August's opening must equal July's, not double it.
    assert august["prior_commission"] == june_commission
    assert august["prior_balance"] == june_commission


def test_referral_commission_counted_once_per_candidate(accounting):
    """Two candidates contribute exactly two allocations, never four."""
    accounting.write_candidates([
        _candidate("c1", "Thrilok", "2026-06", 20_000),
        _candidate("c2", "Thrilok", "2026-06", 20_000),
    ])
    one = cs._carry_forward_balances("2026-07")["thrilok"]["prior_commission"]

    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])
    half = cs._carry_forward_balances("2026-07")["thrilok"]["prior_commission"]

    assert one == 2 * half


def test_salary_accrues_once_per_active_month(accounting):
    """Two prior months of an active salary accrue twice, not once and not thrice."""
    accounting.write_candidates([
        _candidate("c1", "Thrilok", "2026-06", 10_000),
        _candidate("c2", "Thrilok", "2026-07", 10_000),
    ])
    accounting.write_salaries({
        "thrilok": {"reference": "Thrilok", "monthly_salary": 15_000,
                    "active_from": "2026-06", "active_until": None},
    })
    assert cs._carry_forward_balances("2026-08")["thrilok"]["prior_salary"] == 30_000


def test_salary_respects_active_until(accounting):
    """A salary that ended does not keep accruing."""
    accounting.write_candidates([
        _candidate("c1", "Thrilok", "2026-06", 10_000),
        _candidate("c2", "Thrilok", "2026-07", 10_000),
    ])
    accounting.write_salaries({
        "thrilok": {"reference": "Thrilok", "monthly_salary": 15_000,
                    "active_from": "2026-06", "active_until": "2026-06"},
    })
    assert cs._carry_forward_balances("2026-08")["thrilok"]["prior_salary"] == 15_000


def test_generic_operations_admin_is_excluded_from_handler_salary_totals(tmp_path, monkeypatch):
    from features import handler_salaries

    salary_file = tmp_path / "handler_salaries.json"
    salary_file.write_text(
        json.dumps({
            "salaries": {
                "operations admin": {
                    "reference": "Operations Admin", "monthly_salary": 40_000,
                    "active_from": "2026-09", "active_until": "",
                },
                "thrilok": {
                    "reference": "Thrilok", "monthly_salary": 40_000,
                    "active_from": "2026-09", "active_until": "",
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(handler_salaries, "_FILE", str(salary_file))
    monkeypatch.setattr(
        handler_salaries, "_generic_admin_reference_keys",
        lambda: {"operations admin", "operations_admin"},
    )

    owed = handler_salaries.salary_owed_by_handler("2026-09")
    assert set(owed) == {"thrilok"}
    assert handler_salaries.total_salary_owed("2026-09") == 40_000
    # The raw record remains available for operational/audit reconciliation.
    assert {row["reference"] for row in handler_salaries.list_salaries()} == {
        "Operations Admin", "Thrilok",
    }
    with pytest.raises(ValueError, match="not eligible"):
        handler_salaries.set_salary("Operations Admin", 40_000, "2026-09")


def test_payout_reduces_the_balance_exactly_once(accounting):
    """A recorded payout is subtracted once, and a voided one not at all."""
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])
    gross = cs._carry_forward_balances("2026-07")["thrilok"]["prior_balance"]

    accounting.write_expenses([
        {"id": "e1", "reference": "Thrilok", "amount": 5_000,
         "category": "commission", "date": "2026-06-20"},
    ])
    after = cs._carry_forward_balances("2026-07")["thrilok"]
    assert after["prior_paid"] == 5_000
    assert after["prior_balance"] == gross - 5_000


def test_voided_payout_does_not_reduce_the_balance(accounting):
    """A payout reversed by an administrator must stop counting.

    This is how the real ledger avoids double-counting a transfer that is also
    recorded as a referrer recovery.
    """
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])
    accounting.write_expenses([
        {"id": "e1", "reference": "Thrilok", "amount": 5_000, "category": "commission",
         "date": "2026-06-20", "void_status": "RECLASSIFIED_TO_RECOVERY",
         "voided_at": "2026-06-21T00:00:00+00:00",
         "void_reason": "same transaction as recovery le_200d03469da441e0"},
    ])
    assert cs._carry_forward_balances("2026-07")["thrilok"]["prior_paid"] == 0


def test_recovery_reduces_the_balance_exactly_once(accounting):
    """A posted referrer recovery is subtracted once, under the resolved handler key."""
    accounting.write_aliases({"lukka pavan kalyan": "pavan kalyan"})
    accounting.write_candidates([_candidate("c1", "Pavan Kalyan", "2026-06", 20_000)])
    gross = cs._carry_forward_balances("2026-07")["pavan kalyan"]["prior_balance"]

    accounting.write_ledger([
        {"id": "l1", "idempotency_key": "pay_1:X", "action": "referrer_recovery",
         "status": "posted", "amount": 5_000, "payment_date": "2026-06-22",
         "referrer": "LUKKA PAVAN KALYAN"},
    ])
    after = cs._carry_forward_balances("2026-07")["pavan kalyan"]
    # The display name differs from the handler key; the recovery must still land.
    assert after["prior_recoveries"] == 5_000
    assert after["prior_balance"] == gross - 5_000


def test_a_recovery_without_an_alias_lands_on_nobody(accounting):
    """The recovery attaches to a handler only because an alias resolves it.

    In production the ledger records this referrer as "LUKKA PAVAN KALYAN"
    while candidates record "Pavan Kalyan". Rs 10,000 of real recovery reduces
    his balance solely because the referrer registry maps one to the other. Drop
    the alias and the money silently attaches to a handler who has no earnings,
    reducing nothing — so this dependency is asserted rather than assumed.
    """
    accounting.write_aliases({})
    accounting.write_candidates([_candidate("c1", "Pavan Kalyan", "2026-06", 20_000)])
    accounting.write_ledger([
        {"id": "l1", "idempotency_key": "pay_1:X", "action": "referrer_recovery",
         "status": "posted", "amount": 5_000, "payment_date": "2026-06-22",
         "referrer": "LUKKA PAVAN KALYAN"},
    ])

    carried = cs._carry_forward_balances("2026-07")
    assert carried["pavan kalyan"]["prior_recoveries"] == 0
    # It did not vanish — it landed on an unrelated key with nothing to offset.
    assert carried["lukka pavan kalyan"]["prior_recoveries"] == 5_000
    assert carried["lukka pavan kalyan"]["prior_commission"] == 0


def test_complimentary_is_inside_commission_not_added_on_top(accounting):
    """Profile-closure complimentary is a subset of commission, never an addition.

    A closed profile pays the referrer Rs 5,000 on top of their 50% share and
    the closure admin a separate Rs 5,000. Both are already inside
    `prior_commission`; reporting them must not add them a second time.
    """
    accounting.write_candidates([
        _candidate("c1", "Pavan Kalyan", "2026-06", 20_000,
                   service_type="profile_service"),
    ])
    carried = cs._carry_forward_balances("2026-07")

    referrer = carried["pavan kalyan"]
    assert referrer["prior_complimentary"] == 5_000
    assert referrer["prior_complimentary_count"] == 1
    # 10,000 commission + 5,000 complimentary, the complimentary counted inside.
    assert referrer["prior_commission"] == 15_000
    assert referrer["prior_complimentary"] < referrer["prior_commission"]
    assert referrer["prior_balance"] == referrer["prior_commission"]

    # The closure admin earns their own 5,000, and it is likewise not doubled.
    admin = carried["thrilok"]
    assert admin["prior_commission"] == 5_000
    assert admin["prior_complimentary"] == 5_000
    assert admin["prior_balance"] == 5_000


def test_negative_opening_becomes_a_carry_forward_receivable(accounting):
    """Overpaying a handler yields a receivable, not a negative amount to pay."""
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 10_000)])
    earned = cs._carry_forward_balances("2026-07")["thrilok"]["prior_commission"]
    accounting.write_expenses([
        {"id": "e1", "reference": "Thrilok", "amount": earned + 1_500,
         "category": "commission", "date": "2026-06-20"},
    ])

    opening = cs._carry_forward_balances("2026-07")["thrilok"]["prior_balance"]
    assert opening == -1_500

    net_payable = opening  # no activity in July
    assert max(0, net_payable) == 0
    assert max(0, -net_payable) == 1_500


# ---------------------------------------------------------------------------
# 8-10: a missing source must be visible, not silently zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("attribute, source", [
    ("expenses", "handler_expenses"),
    ("salaries", "handler_salaries"),
    ("ledger", "payment_verification_ledger"),
])
def test_missing_accounting_store_marks_the_result_unreconciled(
    accounting, attribute, source,
):
    """Deleting any required store must surface, not vanish into a zero.

    This is the test that would have caught the split defect. Before the fix
    every one of these cases returned a confident balance.
    """
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])
    getattr(accounting, attribute).unlink()

    assert source in cs.unavailable_accounting_sources()

    row = cs._carry_forward_balances("2026-07")["thrilok"]
    assert row["unreconciled"] is True
    assert source in row["unreconciled_sources"]


def test_all_sources_present_is_reconciled(accounting):
    """The flag must stay off in the healthy case, or it is just noise."""
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])

    assert cs.unavailable_accounting_sources() == []
    row = cs._carry_forward_balances("2026-07")["thrilok"]
    assert row["unreconciled"] is False
    assert row["unreconciled_sources"] == []


def test_empty_store_is_not_the_same_as_a_missing_one(accounting):
    """A readable store with no rows is reconciled; an unreadable one is not.

    These two states produce identical arithmetic, which is exactly why they
    have to be distinguished explicitly.
    """
    accounting.write_candidates([_candidate("c1", "Thrilok", "2026-06", 20_000)])

    accounting.write_expenses([])
    assert cs._carry_forward_balances("2026-07")["thrilok"]["unreconciled"] is False

    accounting.expenses.write_text("{ not json", encoding="utf-8")
    row = cs._carry_forward_balances("2026-07")["thrilok"]
    assert row["unreconciled"] is True
    assert "handler_expenses" in row["unreconciled_sources"]


# ---------------------------------------------------------------------------
# 12: the known Jun/Jul/Aug figures
# ---------------------------------------------------------------------------

def test_carry_forward_chain_across_three_months(accounting):
    """Each month's closing becomes the next month's opening, exactly once.

    Mirrors the production shape: commission every month, a salaried handler, a
    payout, and a recovery attributed under a different display name.
    """
    accounting.write_aliases({"lukka pavan kalyan": "pavan kalyan"})
    accounting.write_candidates([
        _candidate("c1", "Thrilok", "2026-06", 40_000),
        _candidate("c2", "Thrilok", "2026-07", 40_000),
        _candidate("c3", "Pavan Kalyan", "2026-06", 20_000),
    ])
    accounting.write_salaries({
        "thrilok": {"reference": "Thrilok", "monthly_salary": 15_000,
                    "active_from": "2026-06", "active_until": None},
    })
    accounting.write_expenses([
        {"id": "e1", "reference": "Thrilok", "amount": 10_000,
         "category": "commission", "date": "2026-06-30"},
    ])
    accounting.write_ledger([
        {"id": "l1", "idempotency_key": "pay_1:X", "action": "referrer_recovery",
         "status": "posted", "amount": 5_000, "payment_date": "2026-06-22",
         "referrer": "LUKKA PAVAN KALYAN"},
    ])

    july = cs._carry_forward_balances("2026-07")
    august = cs._carry_forward_balances("2026-08")

    # June: commission + one month of salary, less the payout.
    assert july["thrilok"]["prior_salary"] == 15_000
    assert july["thrilok"]["prior_paid"] == 10_000
    assert july["thrilok"]["prior_balance"] == (
        july["thrilok"]["prior_commission"] + 15_000 - 10_000
    )

    # August: two months of salary, the same single payout, nothing doubled.
    assert august["thrilok"]["prior_salary"] == 30_000
    assert august["thrilok"]["prior_paid"] == 10_000
    assert august["thrilok"]["prior_commission"] > july["thrilok"]["prior_commission"]

    # The recovery lands on Pavan Kalyan once, in both windows.
    assert july["pavan kalyan"]["prior_recoveries"] == 5_000
    assert august["pavan kalyan"]["prior_recoveries"] == 5_000

    # Nothing is unreconciled when every source is readable.
    assert all(not row["unreconciled"] for row in august.values())
