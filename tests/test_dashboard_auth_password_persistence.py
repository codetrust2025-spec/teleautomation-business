from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as auth
from core.dashboard_auth_api import install_dashboard_auth
from api.routers.data_room import router as data_room_router
from features import data_room_credentials_store as data_room_credentials


def _configure_admin(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "deployment-password")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "independent-session-secret")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(
        auth,
        "_ADMIN_CREDENTIAL_OVERRIDE_FILE",
        str(tmp_path / "auth" / "dashboard_admin.json"),
    )
    monkeypatch.setattr(
        data_room_credentials,
        "sync_admin_login_copy",
        lambda updates: ({"admin": dict(updates)}, None),
    )


def test_changed_admin_password_survives_environment_reload(monkeypatch, tmp_path):
    _configure_admin(monkeypatch, tmp_path)

    assert auth.change_operator_password(
        "operations_admin",
        "deployment-password",
        "changed-password",
    ) is None
    assert auth.verify_credentials("operations_admin", "changed-password") is True
    assert auth.verify_credentials("operations_admin", "deployment-password") is False

    # Container recreation reloads the unchanged deployment environment. The
    # persistent override must continue to win for this exact bootstrap secret.
    monkeypatch.setenv("DASHBOARD_PASSWORD", "deployment-password")
    assert auth.verify_credentials("operations_admin", "changed-password") is True


def test_intentional_deployment_rotation_supersedes_saved_override(monkeypatch, tmp_path):
    _configure_admin(monkeypatch, tmp_path)
    assert auth.change_operator_password(
        "operations_admin",
        "deployment-password",
        "changed-password",
    ) is None

    monkeypatch.setenv("DASHBOARD_PASSWORD", "rotated-by-deployment")

    assert auth.verify_credentials("operations_admin", "rotated-by-deployment") is True
    assert auth.verify_credentials("operations_admin", "changed-password") is False


def test_password_change_fails_closed_when_override_cannot_be_persisted(monkeypatch, tmp_path):
    _configure_admin(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "_persist_admin_credentials_override", lambda *args: False)

    error = auth.change_operator_password(
        "operations_admin",
        "deployment-password",
        "changed-password",
    )

    assert error == "Could not persist the new password"
    assert auth.verify_credentials("operations_admin", "deployment-password") is True
    assert auth.verify_credentials("operations_admin", "changed-password") is False


def test_login_session_and_invalid_password_after_persisted_change(monkeypatch, tmp_path):
    _configure_admin(monkeypatch, tmp_path)
    app = FastAPI()
    install_dashboard_auth(app)
    client = TestClient(app, base_url="https://operations.example.test")

    login = client.post(
        "/auth/login",
        json={"username": "operations_admin", "password": "deployment-password"},
    )
    assert login.status_code == 200
    assert client.get("/auth/status").json()["authenticated"] is True

    changed = client.post(
        "/auth/change-password",
        json={
            "current_password": "deployment-password",
            "new_password": "changed-password",
        },
    )
    assert changed.status_code == 200

    fresh_client = TestClient(app, base_url="https://operations.example.test")
    assert fresh_client.post(
        "/auth/login",
        json={"username": "operations_admin", "password": "deployment-password"},
    ).status_code == 401
    assert fresh_client.post(
        "/auth/login",
        json={"username": "operations_admin", "password": "changed-password"},
    ).status_code == 200


def test_data_room_does_not_expose_a_second_admin_password_change_route():
    assert not any(
        route.path == "/data-room/credentials/admin" and "PATCH" in route.methods
        for route in data_room_router.routes
    )
