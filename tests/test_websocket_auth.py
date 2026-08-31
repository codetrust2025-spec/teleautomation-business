from __future__ import annotations

import pytest


def test_mail_websocket_auth_ping_reconnect_and_cleanup(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import main
    from core import dashboard_auth_vps, recruitment_mail_store, recruitment_realtime

    monkeypatch.setenv("DASHBOARD_PASSWORD", "dashboard-fixture")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "mail-websocket-fixture-secret")
    monkeypatch.setenv("AI_INTERVIEW_OFFER_TRACKING_ENABLED", "true")
    monkeypatch.setattr(recruitment_mail_store, "notification_summary", lambda: {"pending": 0})
    monkeypatch.setattr(
        recruitment_mail_store,
        "list_realtime_events",
        lambda **_: [
            {
                "id": "event-2",
                "event_type": "mail.updated",
                "payload": {"notification_id": "synthetic-1"},
                "created_at": "2026-08-15T00:00:00Z",
            }
        ],
    )
    client = TestClient(main.app)

    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect("/ws/mail-monitoring"):
            pass
    assert denied.value.code == 4403

    token = dashboard_auth_vps.create_session_token("operations-admin")
    headers = {"cookie": f"{dashboard_auth_vps.SESSION_COOKIE}={token}"}
    baseline = recruitment_realtime.connection_count()
    with client.websocket_connect(
        "/ws/mail-monitoring?last_event_id=event-1",
        headers=headers,
    ) as websocket:
        assert websocket.receive_json()["event"] == "connected"
        replay = websocket.receive_json()
        assert replay["event"] == "mail.updated"
        assert replay["event_id"] == "event-2"
        assert replay["delivery_source"] == "replay"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["event"] == "pong"
        assert recruitment_realtime.connection_count() == baseline + 1
    assert recruitment_realtime.connection_count() == baseline

    with client.websocket_connect("/ws/mail-monitoring", headers=headers) as websocket:
        assert websocket.receive_json()["event"] == "connected"
    assert recruitment_realtime.connection_count() == baseline
