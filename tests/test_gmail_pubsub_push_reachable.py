"""Google must be able to deliver a Gmail push notification.

`/api/gmail/pubsub/push` is a machine-to-machine callback: Google Pub/Sub POSTs
to it with no dashboard session cookie. It was missing from the auth
exemptions, and `api` is an API root, so the session middleware answered every
push with 401 before the endpoint ran. Nothing surfaced that: the Gmail watch
registered successfully, the mailbox reported CONNECTED, and no mail ever
arrived. Verified against production before the fix - a loopback POST with no
session returned `{"detail":"Authentication required"}`.

The endpoint is not becoming unauthenticated. It compares
GMAIL_PUBSUB_VERIFICATION_TOKEN with `hmac.compare_digest` before touching a
mailbox, which is the same shape as `/webhooks/whatsapp` - already exempt.

The existing coverage in test_recruitment_api.py could not catch this: its
`app_client` builds a bare FastAPI with only the recruitment routes and no
session middleware, so the push endpoint was only ever tested where no
middleware could reject it. These exercise the real decision function and a
middleware-bearing app.
"""

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as dashboard_auth
from core import recruitment_mail_api
from core.dashboard_auth_api import install_dashboard_auth
from core.recruitment_mail_api import install_recruitment_mail_routes

PUSH_PATH = "/api/gmail/pubsub/push"


def _payload(message_id: str = "m1") -> dict:
    data = base64.b64encode(
        json.dumps({"emailAddress": "candidate@test.invalid", "historyId": "1234"}).encode()
    ).decode()
    return {"message": {"messageId": message_id, "data": data}, "subscription": "projects/x/subscriptions/y"}


def _client(monkeypatch) -> TestClient:
    """An app assembled the way main.py assembles the real one.

    The session middleware and the API-root registration both have to be
    present, because the bug lives in their interaction: `api` is an API root,
    the push path was not exempt, and the middleware refused the request before
    the route was reached. A test app without the middleware cannot observe
    that, which is precisely why the existing push tests stayed green while
    production returned 401.
    """
    monkeypatch.setenv("AI_INTERVIEW_OFFER_TRACKING_ENABLED", "true")
    monkeypatch.setenv("GMAIL_PUBSUB_VERIFICATION_TOKEN", "secret")
    # A password is what switches the dashboard gate on at all.
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-operator-password")
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    app = FastAPI()
    install_dashboard_auth(app)
    install_recruitment_mail_routes(app)
    dashboard_auth.register_api_roots(app)
    assert dashboard_auth.auth_enabled(), "the gate must be on or these tests prove nothing"
    return TestClient(app)


def test_the_push_endpoint_needs_no_dashboard_session():
    """The fix itself, asserted on the decision function the middleware uses.

    Google holds no cookie. If this path is not public, every push is refused
    before the endpoint's own token check can run.
    """
    assert dashboard_auth.is_public_path(PUSH_PATH) is True


def test_it_is_public_for_the_same_reason_the_whatsapp_webhook_is():
    """Both are provider callbacks that authenticate themselves by shared secret."""
    assert dashboard_auth.is_public_path("/webhooks/whatsapp") is True
    # Sibling API paths must stay protected - this exemption is one path, not a
    # prefix, so it must not open up /api/ generally.
    assert dashboard_auth.is_public_path("/api/candidate-mailboxes/health") is False
    assert dashboard_auth.is_public_path("/api/gmail/pubsub") is False


def test_a_missing_or_wrong_verification_token_is_rejected(monkeypatch):
    """Being reachable must not mean being open."""
    client = _client(monkeypatch)
    monkeypatch.setattr(
        recruitment_mail_api.store, "mailbox_by_email",
        lambda email: pytest.fail("a rejected push must never read a mailbox"),
    )
    assert client.post(f"{PUSH_PATH}?token=wrong", json=_payload()).status_code == 403
    assert client.post(PUSH_PATH, json=_payload()).status_code == 403


def test_a_valid_token_reaches_the_handler_and_queues_a_sync(monkeypatch):
    """End of the path: no session, correct token, real work queued."""
    client = _client(monkeypatch)
    jobs: list = []
    monkeypatch.setattr(recruitment_mail_api.store, "mailbox_by_email", lambda email: {"id": "mb1", "candidate_id": "c1"})
    monkeypatch.setattr(recruitment_mail_api.store, "record_pubsub_delivery", lambda *a, **k: True)
    monkeypatch.setattr(recruitment_mail_api.store, "update_mailbox", lambda *a, **k: {})
    monkeypatch.setattr(recruitment_mail_api.store, "enqueue_sync", lambda *a, **k: jobs.append(k) or {"id": "j1"})

    response = client.post(f"{PUSH_PATH}?token=secret", json=_payload())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert jobs and jobs[0]["requested_by"] == "gmail-pubsub"


def test_the_header_form_of_the_token_also_works(monkeypatch):
    """Push subscriptions can carry the token as a header instead of a query."""
    client = _client(monkeypatch)
    monkeypatch.setattr(recruitment_mail_api.store, "mailbox_by_email", lambda email: {"id": "mb1", "candidate_id": "c1"})
    monkeypatch.setattr(recruitment_mail_api.store, "record_pubsub_delivery", lambda *a, **k: True)
    monkeypatch.setattr(recruitment_mail_api.store, "update_mailbox", lambda *a, **k: {})
    monkeypatch.setattr(recruitment_mail_api.store, "enqueue_sync", lambda *a, **k: {"id": "j1"})

    response = client.post(
        PUSH_PATH, json=_payload("m2"),
        headers={"X-TeleAutomation-PubSub-Token": "secret"},
    )
    assert response.status_code == 200, response.text
