"""Dashboard operator access helpers (admin vs handler, ops token)."""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import HTTPException, Request

from core import dashboard_auth_vps as auth


def operator_profile(request: Request) -> dict:
    if is_internal_service_authorized(request):
        return {
            "username": "marketing-service",
            "role": "service",
            "reference": None,
        }
    return auth.operator_profile_from_cookies(dict(request.cookies))


def is_internal_service_authorized(request: Request) -> bool:
    expected = (os.environ.get("INTERNAL_SERVICE_TOKEN") or "").strip()
    supplied = (request.headers.get("X-Internal-Service-Token") or "").strip()
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _ref_key(ref: str | None) -> str:
    return (ref or "").strip().lower() or "unknown"


def is_handler_request(request: Request) -> bool:
    return operator_profile(request).get("role") == "handler"


def handler_forbidden_path(path: str, method: str) -> bool:
    """Handlers have the same API access as admins."""
    return False


def require_fleet_admin(request: Request) -> dict:
    """Raise 403 when an unauthenticated caller hits fleet control APIs."""
    profile = operator_profile(request)
    if not auth.is_admin_profile(profile):
        raise HTTPException(status_code=403, detail="Admin access required")
    return profile


def handler_reference_scope(request: Request, reference: str | None) -> str | None:
    """Handlers are locked to their reference; admins may filter freely."""
    scoped = auth.scoped_reference(operator_profile(request))
    if scoped:
        return scoped
    ref = (reference or "").strip()
    return ref or None


def handler_payout_reference_scope(request: Request, reference: str | None = None) -> str | None:
    """Return the only payout ledger a handler may read.

    Candidate data can be shared operationally, but the payout ledger and
    earnings totals are private to each handler.  This scope is deliberately
    independent from ``handler_reference_scope`` so it cannot be weakened by a
    candidate-list visibility change.
    """
    profile = operator_profile(request)
    if profile.get("role") == "handler":
        return (profile.get("reference") or profile.get("username") or "").strip() or None
    ref = (reference or "").strip()
    return ref or None


def assert_candidate_row_access(request: Request, row: dict) -> None:
    """All authenticated operators may access any candidate row."""
    return


def prepare_candidate_body(request: Request, body: dict) -> dict:
    return dict(body or {})


def is_ops_request_authorized(request: Request) -> bool:
    """Allow scripted recovery when OPS_API_TOKEN matches X-Ops-Token header."""
    expected = (os.environ.get("OPS_API_TOKEN") or "").strip()
    if not expected:
        return False
    got = (request.headers.get("X-Ops-Token") or "").strip()
    if not got:
        auth_hdr = request.headers.get("Authorization") or ""
        if auth_hdr.lower().startswith("bearer "):
            got = auth_hdr[7:].strip()
    return bool(got) and secrets.compare_digest(got, expected)
