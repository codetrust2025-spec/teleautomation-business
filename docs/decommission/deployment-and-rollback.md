# Deployment and rollback — six-feature decommission

Prepared, not executed. Nothing here runs without the owner's explicit
production approval.

## What is being deployed

| | |
|---|---|
| Current production release | `0207819529e0b6ba88a5e61d435a60108d3557bc` (confirm with `/version`) |
| Proposed change | branch `chore/decommission-six-features`, commit `450efc2` |
| Merge commit | created by the PR; **that** SHA is what gets built and recorded |
| Schema change | **none** — no migration added |
| Frontend | rebuilt inside the image; no separate frontend step |

The absence of a migration is the single most important property of this
release: `docker-compose` still runs the `migrate` one-shot, but with all 27
migrations already applied and their checksums unchanged it is a no-op. There
is no DDL, no data movement and therefore no destructive database step.

The ten Mail Audit tables are **left in place**. Dropping them is a separate,
separately-approved action via `scripts/decommissioned_audit_tables.py`; it is
not part of this deployment.

## Deployment steps

1. Merge the PR. Record the merge commit SHA as the approved release.
2. Take a fresh production backup:
   `pg_dump --format=custom` of the Operations database, a checksum manifest of
   `/var/lib/teleautomation-operations`, and protected copies of
   `/etc/teleautomation-operations.env` and the service configuration.
3. Verify the backup by restoring into an isolated database and comparing
   object and row counts against the live database. A backup that has not been
   restored is not a verified backup.
4. Record the rollback target: `curl https://operations.teleautomation.online/version`
   and keep both the SHA and the currently-running image digest.
5. Capture the pre-deploy baseline used in step 12:

   ```sql
   SELECT
     (SELECT count(*) FROM candidates)          AS candidates,
     (SELECT count(*) FROM candidate_mailboxes) AS mailboxes,
     (SELECT count(*) FROM mailbox_messages)    AS messages,
     (SELECT count(*) FROM mail_notifications)  AS notifications;
   ```

   Also run `python -m scripts.decommissioned_audit_tables` and keep its output:
   it is the pre-deploy record of the audit tables' contents.
6. Build the image from the approved SHA with
   `--build-arg RELEASE_SHA=<merge sha>`, and record the digest.
7. Deploy the image. Recreate the Operations service only — Marketing, nginx,
   DNS, TLS, credentials and Telegram sessions are untouched by this change.
8. Health check: `GET /health` returns
   `{"status":"ok","service":"teleautomation-operations"}`, and `GET /version`
   reports the approved SHA. If `/version` reports the old SHA the deploy did
   not take effect — do not proceed on the assumption that it did.
9. Log in to the dashboard and confirm the sidebar shows exactly six items:
   Daily Ops, Candidates, Slot Booking, Mail Alerts, Data Room, AI Mail Review.
10. Smoke-test each retained feature: Daily Ops loads its day, Candidates lists
    and opens a record with its documents, Slot Booking serves `/submit-slot`,
    Mail Alerts lists notifications and opens `/ws/mail-monitoring`, Data Room
    opens a file, AI Mail Review lists mailboxes.
11. OCR: toggle it from the AI Mail Review header, confirm the state persists
    across a reload and that `GET /ai/ocr-policy/audit` records the change with
    the right actor. Set it back to its pre-deploy value.
12. Mail ingestion: confirm a mailbox sync job is claimed and completed after
    the deploy, and that `mailbox_messages` is still growing. Re-run the count
    query from step 5 — candidates, mailboxes, messages and notifications must
    all be greater than or equal to the baseline. Nothing retained may drop.
13. Confirm each removed route returns **404** when authenticated:
    `/ai/daily-briefing`, `/api/mail-outcome-audit/summary`,
    `/api/mail-audit-ai/status`, `/bgv/cases`, `/payments/reconciliation`,
    `/auth/handler-kit`.
14. Integrity: no foreign key violations and no new orphans. The audit tables
    are expected to be present and unchanged.
15. Watch logs for 30 minutes. Any 500, any worker traceback, or a stalled mail
    queue is a rollback trigger.

## Rollback

Rollback is unusually cheap here because the schema never changed.

1. Redeploy the previously recorded image digest for `0207819`.
2. Verify `GET /version` reports `0207819` again.
3. **No migration rollback is required.** No migration was applied, so there is
   nothing to reverse and the database is already in the correct state for the
   old release.
4. **No database restore is required** for this change alone. The step-2 backup
   exists for unrelated failure, not for undoing this release.
5. Re-verify: login, the twelve-item sidebar returns, mail ingestion still
   consumes its queue, and the count query matches the step-5 baseline.

### Migrations that cannot be trivially reversed

None in this release.

The only irreversible action anywhere in this work is
`scripts/decommissioned_audit_tables.py --drop`, which is **not** part of this
deployment and cannot be triggered by it. If it is ever run, restoring the ten
tables requires a database restore from backup; there is no down-migration.
Its own guards require `--i-have-a-verified-backup` for that reason.

## Out of scope, needs a separate Marketing change

`teleautomation-messaging/core/business_compatibility.py` proxies a prefix list
that still contains `ai/daily-briefing`. After this deploy that path returns 404
through the bridge. Nothing calls it, so this is stale configuration rather than
a break, but it should be removed from `PREFIXES` in a future Marketing release.
