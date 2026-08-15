"""Postgres connection helpers (optional — local dev uses JSON files)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

_url_checked = False
_database_url: str | None = None


def _resolve_database_url() -> str | None:
    global _url_checked, _database_url
    if _url_checked:
        return _database_url
    _url_checked = True
    url = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
    _database_url = url or None
    return _database_url


def use_postgres() -> bool:
    return _resolve_database_url() is not None


@contextmanager
def get_connection() -> Iterator[object]:
    url = _resolve_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 is required when DATABASE_URL is set") from exc
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
