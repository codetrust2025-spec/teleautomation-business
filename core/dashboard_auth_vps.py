"""Dashboard operator login (separate from Telegram /login OTP).

Enable by setting DASHBOARD_PASSWORD in the environment or .env file.
When unset, auth is disabled (local dev convenience).

Env:
  DASHBOARD_USERNAME   default: admin
  DASHBOARD_PASSWORD   required to enable auth
  DASHBOARD_AUTH_SECRET optional HMAC secret (defaults to password)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from functools import lru_cache
from typing import Any

import yaml

from core.config import BASE_DIR, DATA_DIR

SESSION_COOKIE = "ta_operations_session"
SESSION_TTL_SEC = 7 * 24 * 3600
_ADMIN_CREDENTIAL_OVERRIDE_FILE = os.path.join(DATA_DIR, "auth", "dashboard_admin.json")

_PUBLIC_EXACT = frozenset({
    "/auth/login",
    "/auth/verify-admin",
    "/auth/reset-password",
    "/auth/status",
    "/health",
    "/version",
    "/favicon.svg",
    "/icons.svg",
    "/sw.js",
    "/webhooks/whatsapp",
    # Google Pub/Sub pushes Gmail mailbox-change notifications here. It is a
    # machine-to-machine callback and carries no dashboard session, so the
    # session middleware rejected every push with 401 while the watches went on
    # reporting themselves healthy - mail monitoring that looks connected and
    # silently receives nothing. The endpoint is not unauthenticated: it
    # compares GMAIL_PUBSUB_VERIFICATION_TOKEN with hmac.compare_digest before
    # doing anything, exactly as /webhooks/whatsapp above does.
    "/api/gmail/pubsub/push",
    "/push/vapid-public-key",
    "/bookings/confirm",
    # Static pages that must be readable without an account: OAuth consent
    # review and app-store listings fetch them anonymously. They were only ever
    # reachable because their roots were missing from _API_ROOTS, so deriving
    # roots from the route table would have started returning 401 for them.
    "/privacy",
    "/terms",
    "/oauth-home",
})

_PUBLIC_PREFIXES = (
    "/assets/",
    "/call/join/",
    "/public/",
)

# First path segment for API routes (must stay in sync with server.py serve_spa).
_API_ROOTS = frozenset({
    "groups", "account", "accounts", "login", "auth", "message", "start", "stop",
    "state", "health", "version", "ws", "inbox", "crm", "stats", "admin", "ai", "candidates",
    "data-room", "public", "bookings",
    "metrics", "alerts", "handler-expenses", "handler-salaries", "voice",
    "webhooks", "whatsapp", "push", "devices", "demo-tools", "workspace", "fleet", "api",
    "payments", "bgv",
})

# Roots discovered from the routes the app actually registers.
#
# _API_ROOTS alone was fail-open: is_spa_shell_request treats an unrecognised
# first segment as a client-side route, so the auth middleware waved the GET
# through and the real API route answered it — anonymously. Every root anyone
# forgot to add here became a silent authorisation hole, which is how
# /payments, /bgv, /company-expenses and /forward-message all ended up readable
# without a session.
#
# Deriving the set from the app inverts that default: a route that exists is
# protected whether or not anyone remembered to list it. _API_ROOTS is kept as a
# seed for paths served outside the APIRoute table (websockets, mounts) and for
# use before registration has run.
_DISCOVERED_API_ROOTS: set[str] = set()


def register_api_roots(app: Any) -> frozenset[str]:
    """Record the first path segment of every route the app registers.

    Call once after all routers are mounted. Paths that are explicitly public
    stay public: this only decides which roots are *API* roots, and
    is_public_path is still consulted first.
    """
    from fastapi.routing import APIRoute

    discovered: set[str] = set()
    for route in getattr(app, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        stripped = route.path.strip("/")
        if not stripped:
            continue
        first = stripped.split("/")[0]
        # A path that starts with a parameter has no fixed root to key on.
        if first.startswith("{"):
            continue
        discovered.add(first)
    _DISCOVERED_API_ROOTS.update(discovered)
    return frozenset(discovered)


def api_roots() -> frozenset[str]:
    """Every first path segment that belongs to the API rather than the SPA."""
    return frozenset(_API_ROOTS | _DISCOVERED_API_ROOTS)


def _refresh_dashboard_env_from_file() -> None:
    """Re-read DASHBOARD_* from .env (uvicorn reload workers may skip dotenv)."""
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.normpath(os.path.join(here, "..", ".env"))
    try:
        if not os.path.isfile(env_path):
            return
        with open(env_path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key.startswith("DASHBOARD_"):
                    os.environ[key] = value
    except OSError:
        pass


def auth_enabled() -> bool:
    _refresh_dashboard_env_from_file()
    return bool(os.environ.get("DASHBOARD_PASSWORD", "").strip())


def _environment_credentials() -> tuple[str, str]:
    _refresh_dashboard_env_from_file()
    username = (os.environ.get("DASHBOARD_USERNAME") or "admin").strip() or "admin"
    password = os.environ.get("DASHBOARD_PASSWORD", "").strip()
    return username, password


def _environment_password_fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _load_admin_credentials_override() -> dict[str, str] | None:
    try:
        with open(_ADMIN_CREDENTIAL_OVERRIDE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    environment_fingerprint = str(raw.get("environment_password_sha256") or "")
    if not username or not password or not environment_fingerprint:
        return None
    return {
        "username": username,
        "password": password,
        "environment_password_sha256": environment_fingerprint,
    }


def _persist_admin_credentials_override(
    username: str,
    password: str,
    environment_password: str,
) -> bool:
    path = _ADMIN_CREDENTIAL_OVERRIDE_FILE
    directory = os.path.dirname(path)
    tmp = f"{path}.{secrets.token_hex(8)}.tmp"
    payload = {
        "username": username,
        "password": password,
        "environment_password_sha256": _environment_password_fingerprint(environment_password),
        "updated_at": int(time.time()),
    }
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(tmp, "x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def get_credentials() -> tuple[str, str]:
    """Return the effective admin credential.

    The deployment environment remains the bootstrap source. A password changed
    through the authenticated dashboard is kept in the Operations data volume
    and remains valid across container recreation. If deployment intentionally
    rotates the configured username or password, the saved override no longer
    matches its environment fingerprint and the new deployment value wins.
    """
    username, password = _environment_credentials()
    override = _load_admin_credentials_override()
    if not override:
        return username, password
    if not secrets.compare_digest(override["username"], username):
        return username, password
    expected_fingerprint = _environment_password_fingerprint(password)
    if not secrets.compare_digest(
        override["environment_password_sha256"],
        expected_fingerprint,
    ):
        return username, password
    return override["username"], override["password"]


@lru_cache(maxsize=1)
def _handler_accounts() -> dict[str, dict[str, str]]:
    """username -> {reference, password} from config/dashboard_handlers.yaml."""
    path = os.path.join(BASE_DIR, "config", "dashboard_handlers.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except OSError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in raw.get("handlers") or []:
        if not isinstance(row, dict):
            continue
        user = str(row.get("username") or "").strip()
        ref = str(row.get("reference") or "").strip()
        pwd = str(row.get("password") or "").strip()
        if user and ref and pwd:
            out[user.lower()] = {"username": user, "reference": ref, "password": pwd}
    return out


def reload_handler_accounts() -> None:
    _handler_accounts.cache_clear()


def _handlers_yaml_path() -> str:
    return os.path.join(BASE_DIR, "config", "dashboard_handlers.yaml")


def _load_handlers_yaml() -> list[dict[str, str]]:
    path = _handlers_yaml_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except OSError:
        return []
    out: list[dict[str, str]] = []
    for row in raw.get("handlers") or []:
        if not isinstance(row, dict):
            continue
        user = str(row.get("username") or "").strip()
        ref = str(row.get("reference") or "").strip()
        pwd = str(row.get("password") or "").strip()
        if user and ref and pwd:
            out.append({"username": user, "reference": ref, "password": pwd})
    return out


def _save_handlers_yaml(rows: list[dict[str, str]]) -> None:
    path = _handlers_yaml_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "handlers": [
            {
                "username": r["username"],
                "reference": r["reference"],
                "password": r["password"],
            }
            for r in rows
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, default_flow_style=False, sort_keys=False)


def _reference_matches(expected: str, provided: str) -> bool:
    a = " ".join(str(expected or "").strip().lower().split())
    b = " ".join(str(provided or "").strip().lower().split())
    if not a or not b:
        return False
    return a == b or a in b or b in a


def admin_add_handler(username: str, reference: str, password: str) -> str | None:
    """Add handler login to dashboard_handlers.yaml."""
    user = str(username or "").strip()
    ref = str(reference or user).strip()
    pwd = str(password or "").strip()
    if not user or not ref or not pwd:
        return "Username, reference, and password are required"
    rows = _load_handlers_yaml()
    key = user.lower()
    if any(r["username"].lower() == key for r in rows):
        return f"Handler already exists: {user}"
    # Earnings buckets by reference, so a second spelling of an existing person
    # splits their balance across two rows. That is exactly how
    # "LUKKA PAVAN KALYAN" became a handler separate from "Pavan Kalyan".
    ref_key = ref.strip().lower()
    existing = next((r for r in rows if r["reference"].strip().lower() == ref_key), None)
    if existing:
        return (
            f"Reference '{ref}' already belongs to handler '{existing['username']}'. "
            "Use that login, or choose a different reference."
        )
    try:
        from features import referrer_registry as _rr

        known = _rr.resolve_referrer(ref)
        canonical = str((known or {}).get("name") or "").strip()
        if canonical and canonical.lower() != ref_key:
            return (
                f"'{ref}' is recorded as an alias of referrer '{canonical}'. "
                f"Use '{canonical}' as the reference so their earnings stay on one row."
            )
    except Exception:
        # Registry unavailable — the username and reference checks above still apply.
        pass
    rows.append({"username": user, "reference": ref, "password": pwd})
    rows.sort(key=lambda r: r["reference"].lower())
    _save_handlers_yaml(rows)
    reload_handler_accounts()
    return None


def admin_remove_handler(username: str) -> str | None:
    user = str(username or "").strip()
    if not user:
        return "Username required"
    rows = _load_handlers_yaml()
    filtered = [r for r in rows if r["username"].lower() != user.lower()]
    if len(filtered) == len(rows):
        return "Handler not found"
    _save_handlers_yaml(filtered)
    reload_handler_accounts()
    return None


def admin_set_handler_password(username: str, password: str) -> str | None:
    user = str(username or "").strip()
    pwd = str(password or "").strip()
    if not user or not pwd:
        return "Username and password are required"
    rows = _load_handlers_yaml()
    found = False
    for row in rows:
        if row["username"].lower() == user.lower():
            row["password"] = pwd
            found = True
            break
    if not found:
        return "Handler not found"
    _save_handlers_yaml(rows)
    reload_handler_accounts()
    return None


def handler_self_reset_password(username: str, reference: str, new_password: str) -> str | None:
    """Self-service reset — username + referrer name must match handler record."""
    user = str(username or "").strip()
    ref = str(reference or "").strip()
    pwd = str(new_password or "").strip()
    if not user or not ref or len(pwd) < 4:
        return "Enter username, referrer name, and a password of at least 4 characters"
    handler = _handler_accounts().get(user.lower())
    if not handler:
        return "Unknown login username — contact your admin"
    if not _reference_matches(handler.get("reference") or "", ref):
        return "Referrer name does not match our records"
    err = admin_set_handler_password(user, pwd)
    if err:
        return err
    try:
        from features import data_room_credentials_store as creds

        creds.update_handler_login(user, {"password": pwd})
    except Exception:
        pass
    return None


def change_operator_password(username: str, current_password: str, new_password: str) -> str | None:
    user = str(username or "").strip()
    cur = str(current_password or "")
    new = str(new_password or "").strip()
    if not user or not cur or len(new) < 4:
        return "Enter current password and a new password of at least 4 characters"
    profile = resolve_operator_login(user, cur)
    if not profile:
        return "Current password is incorrect"
    role = profile.get("role") or "admin"
    if role == "handler":
        err = admin_set_handler_password(user, new)
        if err:
            return err
        try:
            from features import data_room_credentials_store as creds

            creds.update_handler_login(user, {"password": new})
        except Exception:
            pass
        return None
    expected_user, expected_pass = get_credentials()
    if not secrets.compare_digest(user, expected_user) or not secrets.compare_digest(cur, expected_pass):
        return "Current password is incorrect"
    environment_user, environment_pass = _environment_credentials()
    if not secrets.compare_digest(environment_user, expected_user):
        return "Dashboard credential configuration changed; retry the password change"
    if not _persist_admin_credentials_override(expected_user, new, environment_pass):
        return "Could not persist the new password"
    env_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    if os.path.isfile(env_path):
        try:
            lines: list[str] = []
            replaced = False
            with open(env_path, encoding="utf-8") as f:
                for raw in f:
                    if raw.strip().startswith("DASHBOARD_PASSWORD="):
                        lines.append(f"DASHBOARD_PASSWORD={new}\n")
                        replaced = True
                    else:
                        lines.append(raw)
            if not replaced:
                lines.append(f"DASHBOARD_PASSWORD={new}\n")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError:
            pass
    try:
        from features import data_room_credentials_store as creds

        creds.sync_admin_login_copy({"username": expected_user, "password": new})
    except Exception:
        pass
    return None


def _display_name(username: str, role: str, reference: str | None = None) -> str:
    if role == "handler" and str(reference or "").strip():
        return str(reference).strip()
    configured = str(os.environ.get("DASHBOARD_DISPLAY_NAME") or "").strip()
    if configured:
        return configured
    readable = " ".join(str(username or "").replace("_", " ").replace("-", " ").split())
    return readable.title() or "Operations User"


def _complete_profile(profile: dict[str, Any]) -> dict[str, Any]:
    username = str(profile.get("username") or "").strip()
    role = str(profile.get("role") or "admin").strip().lower() or "admin"
    reference = str(profile.get("reference") or "").strip() or None
    display_name = str(profile.get("display_name") or "").strip() or _display_name(username, role, reference)
    account_id = str(profile.get("account_id") or "").strip() or f"{role}:{username.casefold()}"
    session_id = str(profile.get("session_id") or "").strip()
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20] if session_id else ""
    return {
        "username": username,
        "role": role,
        "reference": reference,
        "display_name": display_name,
        "account_id": account_id,
        "session_id_hash": session_hash,
    }


def resolve_operator_login(username: str, password: str) -> dict[str, Any] | None:
    """Return operator profile on success: {username, role, reference}."""
    if not auth_enabled():
        return _complete_profile({"username": "dev", "role": "admin", "reference": None})
    user = str(username or "").strip()
    pwd = str(password or "")
    if not user or not pwd:
        return None
    expected_user, expected_pass = get_credentials()
    if (
        expected_pass
        and secrets.compare_digest(user, expected_user)
        and secrets.compare_digest(pwd, expected_pass)
    ):
        return _complete_profile({"username": expected_user, "role": "admin", "reference": None})
    handler = _handler_accounts().get(user.lower())
    if handler and secrets.compare_digest(pwd, handler["password"]):
        return _complete_profile({
            "username": handler["username"],
            "role": "handler",
            "reference": handler["reference"],
        })
    return None


def verify_credentials(username: str, password: str) -> bool:
    return resolve_operator_login(username, password) is not None


def _secret() -> bytes:
    _refresh_dashboard_env_from_file()
    raw = (
        os.environ.get("DASHBOARD_AUTH_SECRET")
        or os.environ.get("DASHBOARD_PASSWORD")
        or "teleautomation-dev-insecure"
    )
    return raw.encode("utf-8")


def create_session_token(
    username: str,
    *,
    role: str = "admin",
    reference: str | None = None,
    display_name: str | None = None,
    account_id: str | None = None,
) -> str:
    profile = _complete_profile({
        "username": username,
        "role": role,
        "reference": reference,
        "display_name": display_name,
        "account_id": account_id,
    })
    payload = {
        "u": username,
        "exp": int(time.time()) + SESSION_TTL_SEC,
        "role": role,
        "ref": reference or "",
        "name": profile["display_name"],
        "aid": profile["account_id"],
        "sid": secrets.token_urlsafe(18),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return f"{urlsafe_b64encode(raw).decode()}.{urlsafe_b64encode(sig).decode()}"


def parse_session_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    if not auth_enabled():
        return _complete_profile({"username": "dev", "role": "admin", "reference": None})
    try:
        raw_part, sig_part = token.split(".", 1)
        raw = urlsafe_b64decode(raw_part.encode("utf-8"))
        sig = urlsafe_b64decode(sig_part.encode("utf-8"))
        expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        user = str(payload.get("u") or "").strip()
        if not user:
            return None
        role = str(payload.get("role") or "admin").strip() or "admin"
        ref = str(payload.get("ref") or "").strip() or None
        if role == "handler" and not ref:
            handler = _handler_accounts().get(user.lower())
            ref = handler.get("reference") if handler else None
        return _complete_profile({
            "username": user,
            "role": role,
            "reference": ref,
            "display_name": str(payload.get("name") or "").strip() or None,
            "account_id": str(payload.get("aid") or "").strip() or None,
            "session_id": str(payload.get("sid") or "").strip(),
        })
    except Exception:
        return None


def validate_session_token(token: str | None) -> str | None:
    profile = parse_session_token(token)
    return profile.get("username") if profile else None


def operator_profile_from_cookies(cookies: dict) -> dict[str, Any]:
    if not auth_enabled():
        return _complete_profile({"username": "dev", "role": "admin", "reference": None})
    profile = parse_session_token(cookies.get(SESSION_COOKIE))
    if not profile:
        return {"username": None, "role": None, "reference": None}
    return profile


def is_admin_profile(profile: dict[str, Any] | None) -> bool:
    """All authenticated operators (admin + handler) get full dashboard access."""
    return bool((profile or {}).get("username"))


def is_payroll_admin_profile(profile: dict[str, Any] | None) -> bool:
    """Strict role check for payroll, holidays, and attendance administration."""
    return bool((profile or {}).get("username")) and str((profile or {}).get("role") or "").lower() == "admin"


def scoped_reference(profile: dict[str, Any] | None) -> str | None:
    """Optional reference filter from query params only (no forced handler scope)."""
    return None


def is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def is_spa_shell_request(method: str, path: str) -> bool:
    """Allow unauthenticated GET/HEAD for the React shell (login UI is client-side)."""
    if method not in ("GET", "HEAD"):
        return False
    if is_public_path(path):
        return True
    stripped = path.strip("/")
    if not stripped:
        return True
    first = stripped.split("/")[0]
    return first not in api_roots()


def username_from_request_cookies(cookies: dict) -> str | None:
    if not auth_enabled():
        return "dev"
    return validate_session_token(cookies.get(SESSION_COOKIE))


def audit_operator_accounts() -> dict[str, Any]:
    """Return a credential-free inventory of Operations login identities."""
    rows: dict[str, dict[str, Any]] = {}
    admin_username, admin_password = get_credentials()
    admin_profile = _complete_profile({"username": admin_username, "role": "admin", "reference": None})
    rows[admin_username.casefold()] = {
        "name": admin_profile["display_name"],
        "username": admin_username,
        "role": "admin",
        "active": bool(admin_username and admin_password),
        "password_configured": bool(admin_password),
        "account_id": admin_profile["account_id"],
        "account_sources": ["environment/admin override"],
        "orphaned": False,
    }
    for handler in _load_handlers_yaml():
        username = str(handler.get("username") or "").strip()
        profile = _complete_profile({
            "username": username,
            "role": "handler",
            "reference": handler.get("reference"),
        })
        rows[username.casefold()] = {
            "name": profile["display_name"],
            "username": username,
            "role": "handler",
            "active": bool(handler.get("password")),
            "password_configured": bool(handler.get("password")),
            "account_id": profile["account_id"],
            "account_sources": ["config/dashboard_handlers.yaml"],
            "orphaned": False,
        }

    try:
        from features import data_room_credentials_store

        copied = data_room_credentials_store.get_credentials()
        copied_rows = []
        if isinstance(copied.get("admin"), dict):
            copied_rows.append(dict(copied["admin"], role="admin"))
        copied_rows.extend(
            dict(item, role="handler")
            for item in copied.get("handlers") or []
            if isinstance(item, dict)
        )
        for item in copied_rows:
            username = str(item.get("username") or "").strip()
            if not username:
                continue
            key = username.casefold()
            if key in rows:
                rows[key]["account_sources"].append("data-room credential copy")
                continue
            profile = _complete_profile({
                "username": username,
                "role": item.get("role") or "handler",
                "reference": item.get("reference"),
            })
            rows[key] = {
                "name": profile["display_name"],
                "username": username,
                "role": profile["role"],
                "active": False,
                "password_configured": bool(item.get("password")),
                "account_id": profile["account_id"],
                "account_sources": ["data-room credential copy only"],
                "orphaned": True,
            }
    except Exception:
        pass

    reference_groups: dict[str, list[str]] = {}
    for row in rows.values():
        key = str(row.get("name") or "").strip().casefold()
        if key:
            reference_groups.setdefault(key, []).append(row["username"])
    duplicates = [users for users in reference_groups.values() if len(users) > 1]
    return {
        "users": sorted(rows.values(), key=lambda item: (item["role"], item["name"].casefold())),
        "duplicate_identity_groups": duplicates,
        "orphaned_usernames": sorted(row["username"] for row in rows.values() if row["orphaned"]),
    }
