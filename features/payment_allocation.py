"""Authoritative split of verified money between candidate service and BGV.

One payment can settle two different obligations. A candidate on the profile
service with BGV certificates owes ₹20,000 for the service and ₹30,000 for
certificates a third party performs and bills. When ₹30,000 arrives it is not
"₹30,000 of BGV" and not "₹30,000 of service" — it clears the service in full
and leaves ₹10,000 against the BGV balance.

Service is settled first. The candidate is buying the service; the BGV charge is
money the company only mediates, so it is the part left outstanding when a
payment falls short.

Nothing here reads or writes storage. Callers pass the verified total and the
two expected figures, and every consumer — candidate row, referral, company
share — derives from this one answer so they cannot drift apart.
"""
from __future__ import annotations

from typing import Any

# Excess beyond both obligations is never guessed at. Somebody has to say what
# it was for.
UNALLOCATED_EXCESS_REVIEW = "UNALLOCATED_EXCESS_REVIEW_REQUIRED"


def allocate(
    *,
    verified_total: int,
    service_expected: int,
    bgv_expected: int = 0,
    bgv_enabled: bool = False,
) -> dict[str, Any]:
    """Split verified money across service and BGV obligations.

    Without BGV the whole verified total is service money, including anything
    above the expected figure — paying more than the minimum is legitimate and
    stays commissionable, which is existing behaviour and must not regress.
    """
    verified_total = max(0, int(verified_total or 0))
    service_expected = max(0, int(service_expected or 0))
    bgv_expected = max(0, int(bgv_expected or 0))

    if not bgv_enabled or bgv_expected <= 0:
        return {
            "verified_total": verified_total,
            "service_expected": service_expected,
            "service_received": verified_total,
            "service_outstanding": max(0, service_expected - verified_total),
            "bgv_expected": 0,
            "bgv_received": 0,
            "bgv_outstanding": 0,
            "unallocated_excess": 0,
            "needs_excess_review": False,
            "bgv_enabled": False,
        }

    service_received = min(verified_total, service_expected)
    after_service = max(0, verified_total - service_expected)
    bgv_received = min(after_service, bgv_expected)
    excess = max(0, verified_total - service_expected - bgv_expected)
    return {
        "verified_total": verified_total,
        "service_expected": service_expected,
        "service_received": service_received,
        "service_outstanding": max(0, service_expected - service_received),
        "bgv_expected": bgv_expected,
        "bgv_received": bgv_received,
        "bgv_outstanding": max(0, bgv_expected - bgv_received),
        "unallocated_excess": excess,
        "needs_excess_review": excess > 0,
        "excess_state": UNALLOCATED_EXCESS_REVIEW if excess > 0 else "",
        "bgv_enabled": True,
    }


def commissionable_amount(allocation: dict[str, Any]) -> int:
    """Only service money earns commission.

    BGV is a third-party pass-through: it is not company revenue, so it earns
    nothing for the company, the referrer or the handler.
    """
    return max(0, int((allocation or {}).get("service_received") or 0))
