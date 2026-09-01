"""Password hashing for dashboard logins.

Handler passwords were stored and compared as plaintext: the store held what
the person typed, and login was a `compare_digest` against it. Anyone who could
read the file — or the `credentials.json` mirror, or a backup of the volume —
had every operator's password, and because people reuse passwords the blast
radius was never limited to this dashboard.

`scrypt` from the standard library does the work. It is memory-hard, so a
stolen file cannot be attacked at the rate a plain digest allows, and it adds
no dependency: this project ships `cryptography` for mailbox credentials but
nothing for passwords, and reaching for a new package to hash six logins is a
worse trade than using what Python already has.

The stored form names its own parameters:

    scrypt$<n>$<r>$<p>$<salt-b64>$<key-b64>

so the cost can be raised later without invalidating what is already stored,
and so a legacy plaintext row is recognisable by *not* looking like this. That
distinction is what lets the store migrate itself rather than locking everyone
out on the deployment that introduces hashing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

SCHEME = "scrypt"

# 128 * n * r bytes ≈ 16 MiB per verification. Enough to make offline guessing
# expensive, small enough to stay well under OpenSSL's default memory ceiling —
# above it `hashlib.scrypt` raises rather than hashing, which would fail every
# login. `maxmem` is passed explicitly so the margin does not depend on the
# platform's default.
_N = 2 ** 14
_R = 8
_P = 1
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024
_SALT_BYTES = 16


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_DKLEN,
        maxmem=_MAXMEM,
    )


def hash_password(password: str) -> str:
    """Return a self-describing hash of `password`.

    A fresh salt per call, so two people who choose the same password do not
    end up with the same stored value.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, n=_N, r=_R, p=_P)
    return f"{SCHEME}${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def is_hashed(stored: str) -> bool:
    """Whether a stored value is a hash rather than a legacy plaintext password.

    Deliberately strict about the shape: a password someone actually chose that
    happens to begin with "scrypt$" must not be mistaken for a hash, or the
    verification below would reject their real password forever.
    """
    if not isinstance(stored, str) or not stored.startswith(f"{SCHEME}$"):
        return False
    parts = stored.split("$")
    if len(parts) != 6:
        return False
    _, n, r, p, salt, key = parts
    if not (n.isdigit() and r.isdigit() and p.isdigit()):
        return False
    try:
        return bool(_unb64(salt)) and bool(_unb64(key))
    except Exception:
        return False


def verify_password(password: str, stored: str) -> bool:
    """Check `password` against a stored hash, or a legacy plaintext value.

    Accepting plaintext here is what keeps the deployment that introduces
    hashing from locking out every handler. The caller is expected to rehash
    and persist after a legacy match, so each stored password stops being
    readable the first time it is used or read.
    """
    if not isinstance(password, str) or not password or not isinstance(stored, str) or not stored:
        return False
    if not is_hashed(stored):
        return hmac.compare_digest(password, stored)
    _, n, r, p, salt, key = stored.split("$")
    try:
        expected = _unb64(key)
        candidate = _derive(password, _unb64(salt), n=int(n), r=int(r), p=int(p))
    except (ValueError, MemoryError):
        # A malformed or absurdly-parameterised row must fail closed rather
        # than raise into the login handler.
        return False
    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str) -> bool:
    """Whether a stored value should be replaced with a current-cost hash."""
    if not is_hashed(stored):
        return True
    _, n, r, p, _, _ = stored.split("$")
    return (int(n), int(r), int(p)) != (_N, _R, _P)
