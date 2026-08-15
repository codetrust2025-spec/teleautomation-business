from features import candidate_store


def test_duplicate_profile_rows_share_all_mailboxes(monkeypatch):
    monkeypatch.setattr(
        candidate_store,
        "_load",
        lambda *args, **kwargs: {
            "candidates": [
                {
                    "id": "visible-profile",
                    "name": "Ram Charan M S",
                    "phone": "",
                    "service_type": "profile_service",
                },
                {
                    "id": "legacy-profile",
                    "name": "Reddy Charan M S",
                    "phone": "8328646540",
                    "service_type": "profile_service",
                },
            ]
        },
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

    assert candidate_store.candidate_identity_ids("visible-profile") == [
        "legacy-profile",
        "visible-profile",
    ]
    assert candidate_store.candidate_identity_ids("legacy-profile") == [
        "legacy-profile",
        "visible-profile",
    ]


def test_round_wise_row_with_same_name_is_not_a_mailbox_identity(monkeypatch):
    monkeypatch.setattr(
        candidate_store,
        "_load",
        lambda *args, **kwargs: {
            "candidates": [
                {"id": "profile", "name": "Same Name", "service_type": "profile_service"},
                {"id": "round", "name": "Same Name", "service_type": "round_wise"},
            ]
        },
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

    assert candidate_store.candidate_identity_ids("profile") == ["profile"]
