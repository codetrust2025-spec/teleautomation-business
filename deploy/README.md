# Operations deployment runbook

Preparation only; do not execute against production without authorization.

- Linux user: `telebiz`
- release root: `/srv/teleautomation-operations`
- environment: `/etc/teleautomation-operations.env` (mode `0600`)
- writable data: `/var/lib/teleautomation-operations`
- logs: `/var/log/teleautomation-operations`
- bind: `127.0.0.1:8200`
- database/user: create dedicated Operations names during staging provisioning

Build the image and frontend artifact in CI and deploy a versioned image. Start with one API process and bounded recruitment concurrency. Mount only Operations documents and state. Never mount Telegram sessions or Marketing media.

Create a dedicated database/user with a generated password supplied through `psql` variables; revoke public access, grant the application user only its database/schema, run `python -m core.migrations.runner`, and prove that it cannot connect to the Marketing database. Set `DATABASE_URL`, `OPERATIONS_DATA_DIR`, distinct dashboard credentials/secrets, Gmail/AI settings, both public URLs, the internal Marketing URL, and the cross-service token through the protected environment file.

Attendance additionally requires `OPERATIONS_OFFICE_NETWORK_CIDRS` to contain only the approved office public IP/CIDR values and `OPERATIONS_TRUSTED_PROXY_CIDRS` to contain only the direct reverse-proxy peer CIDRs. The application fails closed when either the client address cannot be established safely or the office policy is absent/malformed. Do not infer an office address from access logs and do not expose these values in frontend configuration. `OPERATIONS_ATTENDANCE_EFFECTIVE_DATE` is optional and otherwise defaults to the date migration 028 is first applied.

Preflight: verify resources and current infrastructure, then validate `GET /health`, candidate CRUD on disposable staging data, public slot flows, recruitment mailbox fixtures, Data Room authorization, `/ws/mail-monitoring`, and Operations-to-Marketing delivery using test endpoints/providers only.

Backup with `pg_dump --format=custom`, a checksum manifest of the Operations document tree, and protected copies of environment and service configuration. Verify by restoring to an isolated database and comparing object/row counts and file checksums. Roll back by stopping Operations writes/workers, retaining post-cutover deltas/outbox entries, restoring the prior image and proxy config, then reconciling deltas. This procedure is not verified until exercised on staging.
