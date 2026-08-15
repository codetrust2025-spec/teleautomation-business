"""Checksum-tracked Operations migration runner."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.db.connection import get_connection, use_postgres

MIGRATIONS_DIR = Path(__file__).resolve().parent
LEDGER_TABLE = "operations_schema_migrations"


def apply_migrations() -> list[str]:
    if not use_postgres():
        return []
    applied_now: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} ("
                "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            cur.execute(
                f"SELECT checksum FROM {LEDGER_TABLE} WHERE version=%s",
                (path.name,),
            )
            row = cur.fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"Applied migration checksum changed: {path.name}")
                continue
            cur.execute(sql)
            cur.execute(
                f"INSERT INTO {LEDGER_TABLE}(version, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    for migration in apply_migrations():
        print(migration)
