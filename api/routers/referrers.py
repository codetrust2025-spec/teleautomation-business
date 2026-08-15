"""Operations referrer identity and payment-account routes."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from core.operations_api_helpers import operations_actor, require_admin

router = APIRouter()


@router.get("/api/referrers", dependencies=[Depends(require_admin)])
@router.get("/referrers", dependencies=[Depends(require_admin)])
async def referrers_list():
    from features.referrer_registry import list_payment_accounts, list_referrers

    counts: dict[str, int] = {}
    for account in list_payment_accounts():
        key = str(account.get("referrer_id") or "")
        counts[key] = counts.get(key, 0) + 1
    return {
        "status": "ok",
        "referrers": [
            {**row, "payment_account_count": counts.get(str(row.get("id") or ""), 0)}
            for row in list_referrers(include_inactive=True)
        ],
    }


@router.get("/api/referrers/{referrer_id}/payment-accounts", dependencies=[Depends(require_admin)])
@router.get("/referrers/{referrer_id}/payment-accounts", dependencies=[Depends(require_admin)])
async def payment_accounts_list(referrer_id: str):
    from features.referrer_registry import list_payment_accounts, resolve_referrer

    referrer = resolve_referrer(referrer_id)
    if referrer is None:
        raise HTTPException(status_code=404, detail="Referrer not found")
    return {
        "status": "ok",
        "referrer": referrer,
        "accounts": list_payment_accounts(referrer_id=referrer["id"]),
    }


@router.post("/api/referrers/{referrer_id}/payment-accounts", dependencies=[Depends(require_admin)])
@router.post("/referrers/{referrer_id}/payment-accounts", dependencies=[Depends(require_admin)])
async def payment_account_add(request: Request, referrer_id: str, body: dict = Body(...)):
    from features.referrer_registry import add_payment_account

    try:
        account = add_payment_account(referrer_id, body, actor=operations_actor(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "account": account}


@router.patch("/api/referrer-payment-accounts/{account_id}", dependencies=[Depends(require_admin)])
@router.patch("/referrer-payment-accounts/{account_id}", dependencies=[Depends(require_admin)])
async def payment_account_update(request: Request, account_id: str, body: dict = Body(...)):
    from features.referrer_registry import update_payment_account

    try:
        account = update_payment_account(account_id, body, actor=operations_actor(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "account": account}


@router.delete("/api/referrer-payment-accounts/{account_id}", dependencies=[Depends(require_admin)])
@router.delete("/referrer-payment-accounts/{account_id}", dependencies=[Depends(require_admin)])
async def payment_account_delete(request: Request, account_id: str):
    from features.referrer_registry import remove_unverified_payment_account

    try:
        removed = remove_unverified_payment_account(account_id, actor=operations_actor(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Payment account not found")
    return {"status": "ok"}
