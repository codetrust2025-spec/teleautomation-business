# Backup plan

Status: **commands and verification must be adapted to an inspected staging/production environment; not executed**

Before cutover, independently back up PostgreSQL, source JSON/state, uploaded files, Telegram sessions, environment files, Nginx configuration, and process-manager definitions. Secret-bearing backups must use restrictive ownership and must never enter Git.

Verification requires more than exit code zero:

- Confirm every expected artifact exists and has a plausible nonzero size.
- Inspect dump metadata and list database objects.
- Restore each database dump into an isolated non-production database and run row-count checks.
- Generate file manifests with sizes and SHA-256 checksums and compare a restored copy.
- Compare Telegram-session checksums without opening or logging their contents.
- Verify ownership and permissions after restore.
- Record tool versions, timestamps, source commit/release, and manifest hashes.

Exact commands remain `BLOCKED_PRODUCTION_APPROVAL` until the real database, storage, service, and configuration locations are inspected without revealing secret values.
