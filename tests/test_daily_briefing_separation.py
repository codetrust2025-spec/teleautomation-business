from core.daily_briefing import calculate


def test_business_briefing_survives_without_messaging_crm():
    briefing = calculate(use_ai=False)

    assert briefing["read_only"] is True
    assert briefing["sources"]["business"] == "available"
    assert briefing["sources"]["messaging_crm"] == "unavailable"
    assert briefing["metrics"]["followups_due"] == 0
    assert any("Messaging CRM" in item for item in briefing["recommendations"])


def test_business_briefing_uses_bounded_messaging_summary(monkeypatch):
    from services import messaging_client

    monkeypatch.setattr(
        messaging_client,
        "fetch_crm_operational_summary",
        lambda **_: {
            "status": "ok",
            "followups_due": [{"id": "1", "name": "Lead one"}],
            "stale_leads": [{"id": "2", "name": "Lead two"}],
        },
    )
    briefing = calculate(use_ai=False)

    assert briefing["sources"]["messaging_crm"] == "available"
    assert briefing["metrics"]["followups_due"] == 1
    assert briefing["metrics"]["stale_leads"] == 1
