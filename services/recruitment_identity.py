"""Persisted person identity, independent of the candidate-list display row.

Resolving an identity is read-only. Missing links retain the source ID; a new
slot or a changed list sort must never silently change a lifecycle lock key.
"""
from __future__ import annotations


def resolve(candidate_id: str, links: dict[str, str]) -> str:
    value = str(candidate_id or "").strip()
    seen: set[str] = set()
    while value and links.get(value) and links[value] != value:
        if value in seen:
            raise ValueError("Cyclic recruitment candidate identity links")
        seen.add(value)
        value = links[value]
    return value


def load_links() -> dict[str, str]:
    from core.db.connection import get_connection, use_postgres
    if not use_postgres():
        return {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT alias_candidate_id,canonical_candidate_id FROM candidate_identity_links")
        return {str(alias): str(canonical) for alias, canonical in cur.fetchall()}


def canonical_candidate_id(candidate_id: str) -> str:
    return resolve(candidate_id, load_links())


def aliases(candidate_id: str, links: dict[str, str]) -> set[str]:
    canonical = resolve(candidate_id, links)
    return {canonical, str(candidate_id)} | {
        alias for alias in links if resolve(alias, links) == canonical
    }
