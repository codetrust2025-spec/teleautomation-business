"""The endpoints that changed from public to guarded must still serve their UI.

Closing GET /company-expenses, GET /company-expenses/total and
Operations expense writes proved easy to verify one way: an anonymous
caller is rejected. The half that actually matters to the product is the other
one — the Total expenditure dialog must still load for a logged-in operator.
These drive the real app with a real session cookie so that is not left to a
manual click.
"""

import pytest
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as auth

PASSWORD = "test-password"
SECRET = "test-secret"


@pytest.fixture
def client(monkeypatch):
    import main

    # The real helper re-reads .env on every call, which would overwrite the
    # test credentials and make the result depend on the developer's machine.
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", SECRET)
    assert auth.auth_enabled(), "auth must be on or these tests prove nothing"

    # Keep real expense data out of the test entirely.
    from features import company_expenses

    monkeypatch.setattr(
        company_expenses, "list_expenses", lambda **kw: [], raising=False
    )
    monkeypatch.setattr(
        company_expenses,
        "total_expenditure",
        lambda **kw: {"total": 0, "handler_payouts": 0, "company_expenses": 0},
        raising=False,
    )
    return TestClient(main.app)


def _signed_in(client, role="admin", username="admin"):
    """Starlette deprecates per-request cookies, so set them on the client."""
    client.cookies.clear()
    client.cookies.set(
        auth.SESSION_COOKIE, auth.create_session_token(username, role=role)
    )
    return client


GUARDED_READS = [
    "/company-expenses",
    "/company-expenses/total",
]


@pytest.mark.parametrize("path", GUARDED_READS)
def test_anonymous_is_rejected(client, path):
    response = client.get(path)
    assert response.status_code in (401, 403), response.text
    assert "detail" in response.json()


@pytest.mark.parametrize("path", GUARDED_READS)
def test_a_logged_in_admin_still_gets_the_data(client, path):
    response = _signed_in(client).get(path)
    assert response.status_code == 200, (
        f"{path} rejected an authenticated admin: "
        f"{response.status_code} {response.text[:200]}"
    )
    assert response.json().get("status") == "ok"


@pytest.mark.parametrize("path", GUARDED_READS)
def test_a_logged_in_handler_is_not_locked_out(client, path):
    """_require_fleet_admin passes any authenticated operator, so guarding these
    must not have quietly become admin-only for handlers."""
    response = _signed_in(client, role="handler", username="handler1").get(path)
    assert response.status_code == 200, (
        f"{path} locked out an authenticated handler: {response.status_code}"
    )


def test_a_forged_session_is_refused(client):
    """The guard must rest on the signature, not merely on a cookie existing."""
    client.cookies.set(auth.SESSION_COOKIE, "not.a.real.token")
    response = client.get("/company-expenses")
    assert response.status_code in (401, 403), response.text


def test_writing_company_expenses_still_works_when_signed_in(client, monkeypatch):
    from features import company_expenses

    saved = {}

    def fake_save(body):
        saved.update(body or {})
        return {"id": "test-expense", **saved}

    monkeypatch.setattr(
        company_expenses, "create_expense", fake_save, raising=False
    )

    payload = {"amount": 10, "category": "other", "note": "test"}
    anonymous = client.post("/company-expenses", json=payload)
    assert anonymous.status_code in (401, 403)
    assert saved == {}, "an anonymous caller must not reach the writer at all"

    allowed = _signed_in(client).post("/company-expenses", json=payload)
    assert allowed.status_code == 200, allowed.text
    assert saved == payload
