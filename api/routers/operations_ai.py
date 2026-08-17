"""Operations-owned OCR policy routes.

The daily briefing routes that shared this module were removed with the Daily
Briefing feature. OCR stayed: its control moved out of the decommissioned
Settings page and into AI Mail Review, against these same routes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/ai/ocr-policy")
async def read_ocr_policy(request: Request):
    from core import ocr_policy
    from core.dashboard_access import operator_profile

    operator_profile(request)
    return {"status": "ok", **ocr_policy.status()}


@router.put("/ai/ocr-policy")
async def update_ocr_policy(request: Request, body: dict):
    from core import ocr_policy
    from core.dashboard_access import operator_profile

    profile = operator_profile(request)
    if (profile.get("role") or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can change the OCR policy")
    raw = (body or {}).get("enabled")
    if not isinstance(raw, bool):
        raise HTTPException(status_code=400, detail="'enabled' must be true or false")
    client = getattr(request, "client", None)
    result = ocr_policy.set_ocr_enabled(
        raw,
        actor=str(profile.get("username") or profile.get("name") or "admin"),
        source_ip=getattr(client, "host", "") or "",
    )
    return {"status": "ok", **result}


@router.get("/ai/ocr-policy/audit")
async def read_ocr_policy_audit(request: Request, limit: int = 20):
    from core import ocr_policy
    from core.dashboard_access import operator_profile

    if (operator_profile(request).get("role") or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can read the OCR policy audit")
    return {"status": "ok", "entries": ocr_policy.audit_log(limit)}
