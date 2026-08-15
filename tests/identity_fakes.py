"""A cursor double that answers the identity resolver's queries.

Shared by the resolver tests and the reconciliation tests so both drive the
real ``features.candidate_identity`` logic. Only the database is faked.

Dispatch is on a distinctive table name, and an unrecognised query raises
rather than returning an empty result — a resolver query that changes shape
must fail loudly here instead of silently resolving every candidate to itself.
"""

from __future__ import annotations

from typing import Any


class FakeIdentityCursor:
    def __init__(self, *, candidates=(), links=(), mailboxes=()):
        self.candidates = [dict(row) for row in candidates]
        # Links may be given as (alias, canonical) or with an explicit method
        # and verified flag. A bare pair defaults to a *derived* mapping —
        # the kind migration 010 recomputes — because that is what most rows
        # in production are, and treating them as declared would be the
        # optimistic assumption the resolver must never make.
        self.links = [
            (
                row[0],
                row[1],
                row[2] if len(row) > 2 else "VERIFIED_PHONE",
                row[3] if len(row) > 3 else True,
            )
            for row in links
        ]
        self.mailboxes = list(mailboxes)
        self._result: list[tuple] = []
        self.queries: list[str] = []

    # context-manager form, matching psycopg2 cursor usage
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql: str, params: Any = ()):
        self.queries.append(sql)
        collapsed = " ".join(sql.split())
        if "FROM candidate_identity_links" in collapsed:
            self._result = list(self.links)
        elif "FROM candidate_mailboxes" in collapsed:
            self._result = [
                (cid, email.lower()) for cid, email in self.mailboxes if "@" in email
            ]
        elif "FROM candidates_store" in collapsed:
            self._result = [
                (
                    row.get("id"),
                    row.get("name"),
                    row.get("phone"),
                    row.get("email"),
                    row.get("service_type"),
                    row.get("canonical_candidate_id"),
                    row.get("profile_candidate_id"),
                )
                for row in self.candidates
            ]
        else:  # pragma: no cover - a new query must be taught to this fake
            raise AssertionError(f"FakeIdentityCursor cannot answer: {collapsed[:160]}")

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


def profile_row(cid: str, name: str, phone: str = "", email: str = "", **extra):
    """A profile-service candidate row as candidates_store stores it."""
    return {
        "id": cid,
        "name": name,
        "phone": phone,
        "email": email,
        "service_type": "profile_service",
        **extra,
    }
