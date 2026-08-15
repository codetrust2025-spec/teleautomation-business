"""Postgres persistence for candidates_store (production)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db.connection import get_connection


def pg_load() -> dict:
    snapshot_versions: dict[str, str] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload
                FROM candidates_store
                ORDER BY COALESCE((payload->>'logged_date')::text, ''), id
                """
            )
            rows = []
            for (payload,) in cur.fetchall():
                if isinstance(payload, dict):
                    row = payload
                elif isinstance(payload, str):
                    try:
                        row = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                else:
                    continue
                rows.append(row)
                cid = str(row.get("id") or "").strip()
                if cid:
                    snapshot_versions[cid] = str(row.get("_store_updated_at") or "")
    return {
        "candidates": rows,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "_snapshot_versions": snapshot_versions,
    }


def pg_save(data: dict) -> None:
    candidates = list(data.get("candidates") or [])
    snapshot_versions = dict(data.get("_snapshot_versions") or {})
    save_at = str(data.get("updated_at") or datetime.now(timezone.utc).isoformat())
    current_ids = {
        str(row.get("id") or "").strip()
        for row in candidates
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Serialize candidate-store commits across API/background workers.
            # Individual rows are still protected by their snapshot version,
            # so a stale full-store snapshot cannot erase a concurrent update.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("telegramforward:candidates_store",))
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                cid = (row.get("id") or "").strip()
                if not cid:
                    continue
                payload = dict(row)
                payload["_store_updated_at"] = save_at
                encoded = json.dumps(payload, ensure_ascii=False)
                if cid not in snapshot_versions:
                    cur.execute(
                        """
                        INSERT INTO candidates_store (id, payload)
                        VALUES (%s, %s::jsonb)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (cid, encoded),
                    )
                    continue
                expected_version = snapshot_versions[cid]
                cur.execute(
                    """
                    UPDATE candidates_store
                    SET payload = %s::jsonb
                    WHERE id = %s
                      AND COALESCE(payload->>'_store_updated_at', '') = %s
                    """,
                    (encoded, cid, expected_version),
                )

            # Honor deliberate deletions only when the row is unchanged since
            # this caller loaded it. Concurrently-created/updated rows survive.
            for cid, expected_version in snapshot_versions.items():
                if cid in current_ids:
                    continue
                cur.execute(
                    """
                    DELETE FROM candidates_store
                    WHERE id = %s
                      AND COALESCE(payload->>'_store_updated_at', '') = %s
                    """,
                    (cid, expected_version),
                )
