"""Handler passwords must not be readable from anything we store.

They used to be kept exactly as typed, in two places: the auth store and the
`credentials.json` mirror, both on the same volume. Login was a `compare_digest`
against the stored text. Anyone who could read either file — or a backup of the
volume — had every operator's password, and since people reuse passwords the
damage was never confined to this dashboard.

The awkward part of fixing that is the deployment itself: every existing row is
plaintext, and a build that only understood hashes would lock out every handler
the moment it went live. So verification still accepts a legacy value, and the
store rehashes on the first read that sees one — the passwords stop being
readable without anyone being locked out in between.

`operations_admin` is deliberately untouched: it authenticates against the
deployment environment, not this store.
"""

from __future__ import annotations

import json

import pytest
import yaml

from core import dashboard_auth_vps as auth
from core import password_hashing


@pytest.fixture
def store(monkeypatch, tmp_path):
    volume = tmp_path / "volume"
    image = tmp_path / "image"
    volume.mkdir()
    image.mkdir()
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "session-secret")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "_load_admin_credentials_override", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(volume))
    monkeypatch.setattr(auth, "BASE_DIR", str(image))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])
    monkeypatch.setattr(auth, "_mirror_hashed_passwords", lambda rows: None)
    auth.reload_handler_accounts()
    yield {"volume": volume, "image": image}
    auth.reload_handler_accounts()


def _stored(volume) -> list[dict]:
    path = volume / "auth" / "dashboard_handlers.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("handlers") or []


def _write_legacy(volume, rows):
    """A store as it exists today: passwords exactly as the person typed them."""
    path = volume / "auth" / "dashboard_handlers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"handlers": rows}), encoding="utf-8")


# --- the primitive ----------------------------------------------------------

def test_a_hash_verifies_its_own_password_and_rejects_others():
    stored = password_hashing.hash_password("correct horse")
    assert password_hashing.verify_password("correct horse", stored) is True
    assert password_hashing.verify_password("Correct Horse", stored) is False
    assert password_hashing.verify_password("", stored) is False


def test_the_same_password_hashes_differently_every_time():
    """Without a per-call salt, two people choosing the same password would be
    visibly identical in the file."""
    a = password_hashing.hash_password("same")
    b = password_hashing.hash_password("same")
    assert a != b
    assert password_hashing.verify_password("same", a)
    assert password_hashing.verify_password("same", b)


def test_the_stored_form_contains_nothing_of_the_password():
    stored = password_hashing.hash_password("Sup3rSecret!")
    assert "Sup3rSecret!" not in stored
    assert stored.startswith("scrypt$")


def test_a_password_that_looks_like_a_hash_is_still_treated_as_a_password():
    """`is_hashed` decides whether a stored value is verified or compared. Too
    loose a check and someone whose password starts with 'scrypt$' can never
    log in again."""
    assert password_hashing.is_hashed("scrypt$notreallyahash") is False
    assert password_hashing.is_hashed("scrypt$a$b$c$d$e") is False
    assert password_hashing.verify_password("scrypt$notreallyahash", "scrypt$notreallyahash") is True


def test_a_malformed_stored_hash_fails_closed_rather_than_raising():
    broken = "scrypt$16384$8$1$!!!not-base64!!!$also-not-base64"
    assert password_hashing.verify_password("anything", broken) is False


# --- migration --------------------------------------------------------------

def test_a_legacy_plaintext_password_still_logs_in(store):
    """The deployment that introduces hashing must not lock anyone out."""
    _write_legacy(store["volume"], [
        {"username": "referrer-thrilok", "reference": "Thrilok", "password": "old-plain"},
    ])
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-thrilok", "old-plain") is True
    assert auth.verify_credentials("referrer-thrilok", "wrong") is False


def test_reading_the_store_rehashes_plaintext_and_writes_it_back(store):
    """Migration happens on read, not only on login: waiting for each person to
    sign in would leave everyone else's password readable indefinitely."""
    _write_legacy(store["volume"], [
        {"username": "referrer-venugopal", "reference": "Venugopal", "password": "old-plain"},
    ])
    auth.reload_handler_accounts()
    auth._load_handlers_yaml()

    rows = _stored(store["volume"])
    assert rows[0]["password"] != "old-plain"
    assert password_hashing.is_hashed(rows[0]["password"])
    assert "old-plain" not in yaml.safe_dump({"handlers": rows})


def test_the_password_still_works_after_it_has_been_migrated(store):
    _write_legacy(store["volume"], [
        {"username": "referrer-charan", "reference": "Charan", "password": "old-plain"},
    ])
    auth.reload_handler_accounts()
    auth._load_handlers_yaml()          # migrates
    auth.reload_handler_accounts()      # a later process reads only the hash

    assert auth.verify_credentials("referrer-charan", "old-plain") is True


def test_an_already_hashed_store_is_not_rehashed_on_every_read(store):
    """Rehashing a hash would change the stored value on every read and, worse,
    hash the hash — after which the real password no longer verifies."""
    assert auth.admin_add_handler("referrer-ravinder", "Ravinder", "pw") is None
    first = _stored(store["volume"])[0]["password"]

    auth.reload_handler_accounts()
    auth._load_handlers_yaml()

    assert _stored(store["volume"])[0]["password"] == first
    assert auth.verify_credentials("referrer-ravinder", "pw") is True


# --- the write paths --------------------------------------------------------

def test_creating_a_handler_stores_a_hash_not_the_password(store):
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "chosen-pw") is None

    rows = _stored(store["volume"])
    assert password_hashing.is_hashed(rows[0]["password"])
    assert "chosen-pw" not in yaml.safe_dump({"handlers": rows})
    assert auth.verify_credentials("referrer-thrilok", "chosen-pw") is True


def test_an_admin_password_change_stores_a_hash(store):
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "first") is None
    assert auth.admin_set_handler_password("referrer-thrilok", "second") is None
    auth.reload_handler_accounts()

    rows = _stored(store["volume"])
    assert password_hashing.is_hashed(rows[0]["password"])
    assert "second" not in yaml.safe_dump({"handlers": rows})
    assert auth.verify_credentials("referrer-thrilok", "second") is True
    assert auth.verify_credentials("referrer-thrilok", "first") is False


def test_a_self_service_reset_stores_a_hash(store, monkeypatch):
    monkeypatch.setattr(auth, "stored_handler_password", auth.stored_handler_password)
    assert auth.admin_add_handler("referrer-pavan-kalyan", "Pavan Kalyan", "first") is None

    assert auth.handler_self_reset_password(
        "referrer-pavan-kalyan", "Pavan Kalyan", "reset-pw",
    ) is None
    auth.reload_handler_accounts()

    rows = _stored(store["volume"])
    assert password_hashing.is_hashed(rows[0]["password"])
    assert "reset-pw" not in yaml.safe_dump({"handlers": rows})
    assert auth.verify_credentials("referrer-pavan-kalyan", "reset-pw") is True


# --- the admin account ------------------------------------------------------

def test_the_admin_login_is_untouched_by_hashing(store):
    """`operations_admin` authenticates against the deployment environment.
    Hashing this store must not reach it."""
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "pw") is None

    profile = auth.resolve_operator_login("operations_admin", "admin-password")
    assert profile["role"] == "admin"
    assert auth.verify_credentials("operations_admin", "wrong") is False


# --- the mirror -------------------------------------------------------------

def test_the_mirror_records_the_hash_and_never_the_password(tmp_path, monkeypatch):
    from features import data_room_credentials_store as creds

    monkeypatch.setattr(creds, "_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(tmp_path / "volume"))
    monkeypatch.setattr(auth, "BASE_DIR", str(tmp_path / "image"))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])
    auth.reload_handler_accounts()

    _, err = creds.create_handler_login({
        "username": "referrer-venugopal", "reference": "Venugopal", "password": "chosen-pw",
    })
    assert err is None

    raw = (tmp_path / "credentials.json").read_text(encoding="utf-8")
    assert "chosen-pw" not in raw
    row = next(r for r in json.loads(raw)["handlers"] if r["username"] == "referrer-venugopal")
    assert password_hashing.is_hashed(row["password"])


def test_the_api_response_carries_no_handler_password_at_all(tmp_path, monkeypatch):
    """A hash is not a secret worth shipping to a browser, and a response body
    is one more thing that gets logged."""
    from features import data_room_credentials_store as creds

    monkeypatch.setattr(creds, "_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(tmp_path / "volume"))
    monkeypatch.setattr(auth, "BASE_DIR", str(tmp_path / "image"))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])
    auth.reload_handler_accounts()

    creds.create_handler_login({
        "username": "referrer-thrilok", "reference": "Thrilok", "password": "chosen-pw",
    })
    payload = creds.get_credentials()

    handler = next(h for h in payload["handlers"] if h["username"] == "referrer-thrilok")
    assert "password" not in handler
    assert "chosen-pw" not in json.dumps(payload)


def test_mirror_rename_keeps_hash_and_stable_account_id(tmp_path, monkeypatch):
    from features import data_room_credentials_store as creds

    monkeypatch.setattr(creds, "_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(tmp_path / "volume"))
    monkeypatch.setattr(auth, "BASE_DIR", str(tmp_path / "image"))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])
    auth.reload_handler_accounts()
    creds.create_handler_login({
        "username": "referrer-thrilok", "reference": "Thrilok", "password": "chosen-pw",
    })
    stored_before = auth.stored_handler_password("referrer-thrilok")

    _, err = creds.rename_handler_login("referrer-thrilok", "thrilok")

    assert err is None
    assert auth.verify_credentials("thrilok", "chosen-pw") is True
    assert auth.stored_handler_password("thrilok") == stored_before
    assert auth.stored_handler_account_id("thrilok") == "handler:referrer-thrilok"
    mirror = creds.handler_login_rows()[0]
    assert mirror["username"] == "thrilok"
    assert mirror["account_id"] == "handler:referrer-thrilok"


def test_mirroring_a_hash_does_not_hash_it_again(tmp_path, monkeypatch):
    """`update_handler_login` routes a password through the hasher. Pushing an
    already-hashed value back through it would hash the hash, and the person's
    real password would stop working — so the mirror write must not re-enter
    the auth store."""
    from features import data_room_credentials_store as creds

    monkeypatch.setattr(creds, "_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(tmp_path / "volume"))
    monkeypatch.setattr(auth, "BASE_DIR", str(tmp_path / "image"))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])
    auth.reload_handler_accounts()

    creds.create_handler_login({
        "username": "referrer-charan", "reference": "Charan", "password": "chosen-pw",
    })
    stored_before = auth.stored_handler_password("referrer-charan")

    creds.set_handler_password_mirror("referrer-charan", stored_before)
    auth.reload_handler_accounts()

    assert auth.stored_handler_password("referrer-charan") == stored_before
    assert auth.verify_credentials("referrer-charan", "chosen-pw") is True


# --- persistence, together with the move in the previous change -------------

def test_a_hashed_login_survives_a_container_recreate(store):
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "pw") is None

    import shutil
    shutil.rmtree(store["image"], ignore_errors=True)
    store["image"].mkdir()
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-thrilok", "pw") is True
    assert password_hashing.is_hashed(_stored(store["volume"])[0]["password"])


def test_recovery_from_the_mirror_yields_a_working_hashed_login(store, monkeypatch):
    """Rows recovered after a deployment destroyed the store are hashed on the
    way in, so recovery does not reintroduce plaintext."""
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [
        {"username": "referrer-ravinder", "reference": "Ravinder", "password": "old-plain"},
    ])
    auth.reload_handler_accounts()

    assert auth.verify_credentials("referrer-ravinder", "old-plain") is True
    assert password_hashing.is_hashed(_stored(store["volume"])[0]["password"])


# --- logging ----------------------------------------------------------------

def test_logging_in_writes_neither_the_password_nor_the_hash_to_the_log(store, caplog):
    assert auth.admin_add_handler("referrer-thrilok", "Thrilok", "chosen-pw") is None
    stored = auth.stored_handler_password("referrer-thrilok")

    with caplog.at_level(0):
        auth.resolve_operator_login("referrer-thrilok", "chosen-pw")
        auth.resolve_operator_login("referrer-thrilok", "wrong-pw")

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "chosen-pw" not in text
    assert stored not in text


def test_load_time_migration_mirrors_without_hashing_the_hash(tmp_path, monkeypatch):
    """The real hazard, at the place it can actually happen.

    Migrating on read also pushes the new hash into the mirror. Doing that
    through `update_handler_login` would route the hash back through
    `admin_set_handler_password`, which hashes what it is given — storing a
    hash of a hash, after which the person's real password no longer verifies.

    The other mirror test calls the mirror writer directly, so it cannot see
    which function the migration chose; this one drives the migration itself.
    """
    from features import data_room_credentials_store as creds

    volume = tmp_path / "volume"
    volume.mkdir()
    monkeypatch.setattr(creds, "_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-password")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setattr(auth, "_load_admin_credentials_override", lambda: None)
    monkeypatch.setattr(auth, "DATA_DIR", str(volume))
    monkeypatch.setattr(auth, "BASE_DIR", str(tmp_path / "image"))
    monkeypatch.setattr(auth, "_handlers_from_credentials_copy", lambda: [])

    # A pre-hashing world: plaintext in the auth store and in the mirror.
    _write_legacy(volume, [
        {"username": "referrer-thrilok", "reference": "Thrilok", "password": "old-plain"},
    ])
    creds._save({"handlers": [{
        "username": "referrer-thrilok", "reference": "Thrilok",
        "password": "old-plain", "role": "handler",
    }]})
    auth.reload_handler_accounts()

    auth._load_handlers_yaml()          # migrates, and mirrors the hash onward
    auth.reload_handler_accounts()      # a later process sees only what was stored

    assert auth.verify_credentials("referrer-thrilok", "old-plain") is True

    raw = (tmp_path / "credentials.json").read_text(encoding="utf-8")
    assert "old-plain" not in raw
    mirrored = json.loads(raw)["handlers"][0]["password"]
    assert password_hashing.is_hashed(mirrored)
    # The mirror holds the same hash the auth store does, not a hash of it.
    assert mirrored == auth.stored_handler_password("referrer-thrilok")
