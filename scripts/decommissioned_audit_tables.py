"""Inspect, and only on explicit instruction drop, the Mail Audit tables.

Mail Audit was decommissioned. Its ten tables are now inert: no runtime module
references them, so they cost nothing but disk. They are *not* dropped by the
application, and deliberately not by a migration either — the migration runner
in ``core/migrations`` applies every ``NNN_*.sql`` automatically at startup, so
a drop placed there would execute itself on the next deploy. Destroying audit
findings and human approval decisions must never be a side effect of deploying.

Default mode is ``report``: it counts rows and proves no retained table depends
on these tables. Dropping additionally requires ``--drop``, the exact database
name via ``--confirm-database``, and ``--i-have-a-verified-backup``.

    python -m scripts.decommissioned_audit_tables                  # report only
    python -m scripts.decommissioned_audit_tables --drop \
        --confirm-database operations --i-have-a-verified-backup

A drop is irreversible: restore from backup is the only way back.
"""

from __future__ import annotations

import argparse
import sys

from core.db.connection import get_connection, use_postgres

# Child-before-parent, so the drop succeeds without CASCADE and any unexpected
# dependency surfaces as an error instead of being silently removed with it.
AUDIT_TABLES = (
    "mail_audit_ai_log",
    "mail_audit_ai_results",
    "mail_audit_ai_queue",
    "mail_outcome_audit_cleanup_log",
    "mail_outcome_audit_finding_history",
    "mail_outcome_audit_approvals",
    "mail_outcome_audit_gaps",
    "mail_outcome_audit_candidates",
    "mail_outcome_audit_findings",
    "mail_outcome_audit_runs",
)


def _existing(cur) -> list[str]:
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name = ANY(%s)",
        (list(AUDIT_TABLES),),
    )
    return sorted(row[0] for row in cur.fetchall())


def _row_counts(cur, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        # Table names come from the fixed tuple above, never from user input.
        cur.execute(f'SELECT count(*) FROM "{table}"')
        counts[table] = int(cur.fetchone()[0])
    return counts


def inbound_dependencies(cur) -> list[tuple[str, str, str]]:
    """Foreign keys pointing *into* the audit tables from anywhere else.

    This is the check that decides whether dropping is safe. Keys pointing out
    of the audit tables into retained ones are expected and harmless.
    """
    cur.execute(
        """
        SELECT tc.table_name, kcu.column_name, ccu.table_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_name = ANY(%s)
          AND tc.table_name <> ALL(%s)
        """,
        (list(AUDIT_TABLES), list(AUDIT_TABLES)),
    )
    return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def report() -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        database = cur.fetchone()[0]
        present = _existing(cur)
        missing = [t for t in AUDIT_TABLES if t not in present]
        counts = _row_counts(cur, present)
        inbound = inbound_dependencies(cur)

    print(f"database: {database}")
    print(f"audit tables present: {len(present)} of {len(AUDIT_TABLES)}")
    for table in present:
        print(f"  {table:<38} {counts[table]:>10,} rows")
    for table in missing:
        print(f"  {table:<38} {'absent':>10}")
    print(f"total rows: {sum(counts.values()):,}")

    if inbound:
        print("\nBLOCKED - retained tables still reference the audit tables:")
        for source, column, target in inbound:
            print(f"  {source}.{column} -> {target}")
        return 1
    print("\nno inbound foreign keys: dropping would orphan nothing")
    return 0


def drop(*, confirm_database: str) -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        database = cur.fetchone()[0]
        if database != confirm_database:
            print(f"refusing: connected to '{database}', not '{confirm_database}'")
            return 1

        inbound = inbound_dependencies(cur)
        if inbound:
            print("refusing: inbound foreign keys exist")
            for source, column, target in inbound:
                print(f"  {source}.{column} -> {target}")
            return 1

        present = _existing(cur)
        counts = _row_counts(cur, present)
        print(f"dropping {len(present)} tables holding {sum(counts.values()):,} rows")
        for table in AUDIT_TABLES:
            if table not in present:
                continue
            cur.execute(f'DROP TABLE "{table}"')
            print(f"  dropped {table} ({counts[table]:,} rows)")

        remaining = _existing(cur)
    print("done" if not remaining else f"still present: {remaining}")
    return 0 if not remaining else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", action="store_true", help="actually drop the tables")
    parser.add_argument("--confirm-database", default="", help="database name, must match")
    parser.add_argument("--i-have-a-verified-backup", action="store_true")
    args = parser.parse_args(argv)

    if not use_postgres():
        print("DATABASE_URL is not set; nothing to inspect")
        return 1
    if not args.drop:
        return report()
    if not args.i_have_a_verified_backup:
        print("refusing: --i-have-a-verified-backup is required; the drop is irreversible")
        return 1
    if not args.confirm_database:
        print("refusing: --confirm-database is required")
        return 1
    return drop(confirm_database=args.confirm_database)


if __name__ == "__main__":
    sys.exit(main())
