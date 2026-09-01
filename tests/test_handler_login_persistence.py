"""Handler logins must outlive the container they were created in.

The store sat under `BASE_DIR` — `/app/config/dashboard_handlers.yaml` inside
the container. `/app` comes from the image and is not mounted, so every
deployment recreated the container and destroyed every handler login. Nothing
re-seeded it: the only writers are the three admin functions, and startup does
migrations and workers.

The failure was silent and worse than plain loss. `create_handler_login` writes
to both the YAML and `credentials.json`, and only the latter is on the volume —
so after a deploy the admin screen still listed every handler while not one of
them could log in.

The admin password hit the same wall and was moved to `DATA_DIR`
(`test_dashboard_auth_password_persistence.py`); handlers were left behind.
These tests pin the move, the two recovery routes, and the cases where recovery
must *not* fire.
"""

from __future__ import annotations

import os
import stat

import pytest

from core import dashboard_auth_vps as auth


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Separate 'volume' and 'image' directories, as the container has."""
    volume = tmp_path / "volume"
    image = tmp_path / "image"
    volume.mkdir()
    image.mkdir()
    # Auth off is a dev convenience that logs everyone in as admin, which would
    # make every assertion below pass without proving anything.
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "session-secret")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "_load_admin_credentials_override", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(volume))
    monkeypatch.setattr(auth, "BASE_DIR", str(image))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])
    auth.reload_handler_accounts()
    yield {"volume": volume, "image": image}
    auth.reload_handler_accounts()


def _legacy_store(image, rows):
    """Write the store where it used to live, as a pre-move container has it."""
    import yaml

    path = image / "config" / "dashboard_handlers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"handlers": rows}), encoding="utf-8")
    return path


def test_the_store_is_written_to_the_volume_not_the_image(store):
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "pw") is None

    assert os.path.isfile(store["volume"] / "auth" / "dashboard_handlers.yaml")
    assert not os.path.exists(store["image"] / "config" / "dashboard_handlers.yaml")


def test_a_handler_added_today_still_logs_in_after_a_deployment(store, monkeypatch):
    """The regression itself: recreating the container discards the image
    layer, and the login must still work off the volume."""
    assert auth.admin_add_handler("referrer-venugopal", "Venugopal", "pw") is None
    assert auth.verify_credentials("referrer-venugopal", "pw") is True

    # A deployment: the image-side directory is gone, the volume is not, and
    # the new process starts with an empty cache.
    import shutil
    shutil.rmtree(store["image"], ignore_errors=True)
    store["image"].mkdir()
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-venugopal", "pw") is True
    profile = auth.resolve_operator_login("referrer-venugopal", "pw")
    assert profile["role"] == "handler"
    assert profile["reference"] == "Venugopal"


def test_a_store_left_in_the_old_location_is_migrated_on_first_read(store):
    """A container that has not been recreated yet still holds the old file."""
    _legacy_store(store["image"], [
        {"username": "referrer-charan", "reference": "Charan", "password": "pw"},
    ])

    assert auth.verify_credentials("referrer-charan", "pw") is True
    # Migrated, not merely read through: the next container will not have the
    # old file at all.
    assert os.path.isfile(store["volume"] / "auth" / "dashboard_handlers.yaml")


def test_logins_a_past_deployment_already_destroyed_are_recovered(store, monkeypatch):
    """The old file is gone — destroyed by the deploy that shipped this move.

    `credentials.json` is on the volume and still holds the rows, so it is the
    only route back for handlers created before the fix landed.
    """
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [
        {"username": "referrer-pavan-kalyan", "reference": "Pavan Kalyan", "password": "pw"},
    ])
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-pavan-kalyan", "pw") is True
    assert auth.resolve_operator_login("referrer-pavan-kalyan", "pw")["reference"] == "Pavan Kalyan"


def test_the_old_store_wins_over_the_mirror(store, monkeypatch):
    """Where both exist the auth store is authoritative — the mirror can carry
    a stale password, and logging someone in with it would be wrong."""
    _legacy_store(store["image"], [
        {"username": "referrer-ravinder", "reference": "Ravinder", "password": "current"},
    ])
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [
        {"username": "referrer-ravinder", "reference": "Ravinder", "password": "stale"},
    ])
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-ravinder", "current") is True
    assert auth.verify_credentials("referrer-ravinder", "stale") is False


def test_removing_the_last_handler_is_not_undone_by_recovery(store, monkeypatch):
    """An emptied store is a decision, not an absent store.

    Recovery keyed on "no handlers" rather than "no file" would resurrect
    everyone an admin had just removed.
    """
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "pw") is None
    assert auth.admin_remove_handler("referrer-thrilok") is None
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [
        {"username": "referrer-thrilok", "reference": "Thrilok", "password": "pw"},
    ])
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-thrilok", "pw") is False


def test_the_store_is_not_readable_by_anything_else_on_the_volume(store):
    """It holds passwords exactly as typed, so the mode is part of the fix."""
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "pw") is None

    path = store["volume"] / "auth" / "dashboard_handlers.yaml"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_a_corrupt_store_does_not_take_the_admin_login_down_with_it(store):
    """A truncated or hand-edited file must fail closed for handlers only."""
    path = store["volume"] / "auth" / "dashboard_handlers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("handlers: [{unclosed\n", encoding="utf-8")
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-thrilok", "pw") is False
    assert auth._load_handlers_yaml() == []


def test_the_admin_account_is_untouched_by_any_of_this(store):
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "pw") is None

    profile = auth.resolve_operator_login("operations_admin", "admin-password")
    assert profile["role"] == "admin"
    assert profile["reference"] is None
