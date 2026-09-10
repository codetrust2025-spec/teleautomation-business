from core import recruitment_mail_store as store


def test_calendar_recovery_discovery_is_read_only_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "use_postgres", lambda: False)

    report = store.calendar_invite_recovery_discovery()

    assert report == {
        "summary": {
            "total": 0,
            "recovery_candidates": 0,
            "already_represented": 0,
            "stale_or_cancelled": 0,
            "already_assessed": 0,
        },
        "records": [],
    }
