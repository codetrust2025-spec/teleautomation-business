# Production runbook — six-feature decommission

**Status: PREPARED, NOT EXECUTED.** Nothing below runs without explicit
approval. Every fact was verified against the live host on 2026-08-17;
statements about paths, ports, images and row counts are measured, not assumed.

Host `187.127.169.159`, all commands as `root` unless noted.

## 0. Facts verified on the live host

| | |
|---|---|
| Live Operations release | `0207819529e0b6ba88a5e61d435a60108d3557bc` (`/version`, and `RELEASE_SHA` inside the running container) |
| Compose project | `teleautomation-production` |
| Compose files | `/opt/teleautomation/docker-compose.production.yml` + `teleautomation-messaging/docker-compose.production.yml` |
| Env file | `/opt/teleautomation/.env.production` (0600 root — never printed, never edited) |
| Operations API container | `teleautomation-production-operations-api-1` → `127.0.0.1:8210` |
| Operations DB container | `teleautomation-production-operations-db-1` |
| Current images | api `962eaf675398`, migrate `e9c464cd5040` (built 2026-08-16 19:07) |
| nginx | one site `teleautomation-production`; operations → `8210`, marketing → `8110` |
| Disk | 153G/193G used, **41G free (80%)** |
| Schema | 40 tables, 32 foreign keys, 27 migrations applied (last 2026-08-16 19:29) |

### Two things that will break a naive deploy

**1. The compose build contexts do not exist.**
Compose resolves them to `/opt/teleautomation-business` and
`/opt/teleautomation-messaging`; both are **missing**. The sources were moved to
`/opt/teleautomation/teleautomation-{business,messaging}` after the images were
built. A plain `docker compose build` fails immediately. Step 6 stages the
source at the path compose expects.

**2. There is no image registry.**
Images are built on the host. Rollback therefore depends on the current image
IDs still existing, which is why step 4 records them and step R1 uses them
directly. The old source also survives at
`/opt/teleautomation/teleautomation-business` (`RELEASE_SHA` = `0207819…`), so a
rebuild-based rollback remains possible if the images are ever pruned.

## 1. The commit being deployed

Branch `chore/decommission-six-features` carries three commits on top of the
live release. Only the first changes shipped code; the other two are docs.

```
450efc2  decommission six features, and move OCR into AI Mail Review   <- code
ec218c1  add the deployment and rollback plan for the decommission     <- docs
09f0ff7  add the production runbook, verified against the live host    <- docs
```

`PROJECT_RULES.md` and `CLAUDE.md` both forbid deploying from an unmerged
branch, so the deployable artifact is the **merge commit on `main`**, not
`ec218c1`. Open the PR, let CI pass, merge, then:

```bash
git -C <repo> fetch origin && git -C <repo> rev-parse origin/main
```

Everything below calls that value `$NEW_SHA`. Do not substitute a branch tip.

## 2–5. Pre-deployment (run in order, all on the host)

### 2. Confirm what is live right now

```bash
curl -s https://operations.teleautomation.online/version
```

Must report `0207819529e0b6ba88a5e61d435a60108d3557bc`. If it reports anything
else, stop: the rollback target in this document is wrong.

### 3. Fresh backup of the production Operations database

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /opt/teleautomation-backups/pre-decommission-$TS
docker exec teleautomation-production-operations-db-1 sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > /opt/teleautomation-backups/pre-decommission-$TS/operations.dump
```

### 4. Verify the backup, and record the rollback target

```bash
ls -lh /opt/teleautomation-backups/pre-decommission-$TS/operations.dump
pg_restore --list /opt/teleautomation-backups/pre-decommission-$TS/operations.dump | wc -l
docker run --rm -v /opt/teleautomation-backups/pre-decommission-$TS:/b postgres:16 \
  pg_restore --list /b/operations.dump | grep -c "TABLE DATA"
```

A non-zero object count and a readable table-of-contents is the check; a dump
that cannot be listed is not a backup. Then record the rollback target:

```bash
docker images --no-trunc --format '{{.Repository}} {{.ID}}' \
  | grep teleautomation-production-operations \
  | tee /opt/teleautomation-backups/pre-decommission-$TS/images.txt
cp /opt/teleautomation/teleautomation-business/RELEASE_SHA \
   /opt/teleautomation-backups/pre-decommission-$TS/previous-release-sha.txt
```

Expected: api `962eaf675398`, migrate `e9c464cd5040`, previous SHA `0207819…`.

### 5. Capture the production baseline

```bash
docker exec -i teleautomation-production-operations-db-1 sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' \
  < baseline.sql | tee /opt/teleautomation-backups/pre-decommission-$TS/baseline.txt
```

Measured 2026-08-17 — step 13 compares against these exact numbers:

| retained table | rows | | Mail Audit table | rows |
|---|---:|---|---|---:|
| candidates_store | 195 | | mail_outcome_audit_findings | 12,759 |
| candidate_mailboxes | 33 | | mail_outcome_audit_finding_history | 12,897 |
| mailbox_messages | 15,252 | | mail_outcome_audit_cleanup_log | 12,651 |
| mailbox_attachments | 3,515 | | mail_outcome_audit_runs | 630 |
| mail_monitoring_notifications | 214 | | mail_outcome_audit_gaps | 615 |
| ai_recruitment_events | 710 | | mail_outcome_audit_candidates | 21 |
| mailbox_sync_jobs | 57,503 | | mail_audit_ai_queue | 15 |
| mail_realtime_events | 114,118 | | mail_audit_ai_results | 10 |
| recruitment_audit_log | 3,305 | | mail_outcome_audit_approvals | **4** |
| | | | mail_audit_ai_log | 0 |

The audit tables hold **39,602 rows, including 4 human approval decisions**.
They must read identically after the deploy. This is the concrete reason the
drop was never put in a migration.

## 6. No migration, no destructive DDL

Verified, not asserted:

- `git diff 0207819..chore/decommission-six-features -- core/migrations/` is **empty**.
- Migration file count is **27 on both sides**.
- The migration ledger already records **27 applied**, so `operations-migrate`
  re-runs, finds nothing new, and exits 0. This is the same idempotency CI
  asserts on every push.
- The only `DROP TABLE` anywhere in the diff is inside
  `scripts/decommissioned_audit_tables.py`. Nothing imports it, no compose
  service invokes it, and it refuses to run without `--drop`,
  `--confirm-database` and `--i-have-a-verified-backup`. It **is not run by this
  deployment** and cannot be triggered by it.

**This release performs zero DDL.** Per instruction,
`scripts/decommissioned_audit_tables.py` is not executed and the ten historical
Mail Audit tables are left untouched.

## 7. Deployment — Operations only

Marketing is not rebuilt, restarted, or reconfigured. Its build context is also
missing, which is exactly why the build below names the Operations services
explicitly instead of building everything.

```bash
cd /opt/teleautomation
NEW_SHA=<merge commit sha from step 1>
TS=<timestamp from step 3>

# 7a. Stage the approved source where compose expects it.
#     Built from a git archive of the exact commit, so there is no .git and no
#     chance of deploying a dirty tree.
rm -rf /opt/teleautomation-business.new
mkdir -p /opt/teleautomation-business.new
tar -xf /tmp/operations-$NEW_SHA.tar -C /opt/teleautomation-business.new
echo "$NEW_SHA" > /opt/teleautomation-business.new/RELEASE_SHA
[ -e /opt/teleautomation-business ] && mv /opt/teleautomation-business /opt/teleautomation-business.prev-$TS
mv /opt/teleautomation-business.new /opt/teleautomation-business

# 7b. Build ONLY the Operations images.
RELEASE_SHA_OPERATIONS=$NEW_SHA \
docker compose -f docker-compose.production.yml \
               -f teleautomation-messaging/docker-compose.production.yml \
               --env-file .env.production \
  build operations-migrate operations-api

# 7c. Bring up ONLY Operations. operations-db and operations-migrate are its
#     declared dependencies; no Marketing service is named or recreated.
RELEASE_SHA_OPERATIONS=$NEW_SHA \
docker compose -f docker-compose.production.yml \
               -f teleautomation-messaging/docker-compose.production.yml \
               --env-file .env.production \
  up -d --no-deps --force-recreate operations-api
```

The tarball in 7a comes from a clean checkout, never a working directory:

```bash
git -C <repo> archive --format=tar "$NEW_SHA" -o /tmp/operations-$NEW_SHA.tar
scp /tmp/operations-$NEW_SHA.tar root@187.127.169.159:/tmp/
```

Do **not** touch `/etc/nginx`, DNS, TLS, `.env.production`, Telegram sessions,
or any `marketing-*` service. Nothing in this change requires them.

## 8–13. Post-deployment smoke tests

### 8. Health and release

```bash
curl -s https://operations.teleautomation.online/health
curl -s https://operations.teleautomation.online/version
docker ps --filter name=teleautomation-production-operations-api-1
```

`/version` must report `$NEW_SHA`. **If it still reports `0207819…` the deploy
did not take effect** — do not continue and do not assume it did.

### 9. Login and session

```bash
curl -s -c /tmp/ops.jar -X POST https://operations.teleautomation.online/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<admin>","password":"<password>"}' -o /dev/null -w '%{http_code}\n'
curl -s -b /tmp/ops.jar https://operations.teleautomation.online/auth/status
```

Expect 200 and the signed-in username. A wrong password must return 401.

### 10. Retained features

```bash
for p in \
  /candidates/interviews/filter-options \
  /candidates?limit=5 \
  /api/mail-monitoring/notifications?limit=5 \
  /api/mail-monitoring/summary \
  /api/mail-monitoring/booking-audit?limit=5 \
  /data-room /data-room/stats \
  /api/candidate-mailboxes/overview /api/candidate-mailboxes/health \
  /api/ai-recruitment/review /api/ai-recruitment/dashboard \
  /api/offer-verification?limit=5 ; do
  printf '%-52s %s\n' "$p" \
    "$(curl -s -b /tmp/ops.jar -o /dev/null -w '%{http_code}' \
       "https://operations.teleautomation.online$p")"
done
curl -s -o /dev/null -w 'submit-slot %{http_code}\n' https://operations.teleautomation.online/submit-slot
curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
  -o /dev/null -w 'bookings/confirm %{http_code}\n' \
  https://operations.teleautomation.online/bookings/confirm
```

All must be 200, `/submit-slot` 200, and `/bookings/confirm` **422** (rejected,
creating nothing). Then open the UI and confirm the sidebar shows exactly:
Daily Ops, Candidates, Slot Booking, Mail Alerts, Data Room, AI Mail Review.

### 11. OCR from AI Mail Review

```bash
curl -s -b /tmp/ops.jar https://operations.teleautomation.online/ai/ocr-policy
curl -s -b /tmp/ops.jar -X PUT https://operations.teleautomation.online/ai/ocr-policy \
  -H 'Content-Type: application/json' -d '{"enabled":true}'
curl -s -b /tmp/ops.jar https://operations.teleautomation.online/ai/ocr-policy
curl -s -b /tmp/ops.jar 'https://operations.teleautomation.online/ai/ocr-policy/audit?limit=3'
```

Toggle from the AI Mail Review header in the browser, confirm it persists across
a reload and appears in the audit trail, then **set it back to its pre-deploy
value**.

### 12. Mail monitoring and WebSocket

```bash
# ingestion still consuming its queue
docker exec -i teleautomation-production-operations-db-1 sh -c \
 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "
  SELECT status, count(*) FROM mailbox_sync_jobs
  WHERE created_at > now() - interval '"'"'2 hours'"'"' GROUP BY status"'

# authenticated upgrade must be 101, anonymous 403
curl -s -i -b /tmp/ops.jar -o /dev/null -w 'authenticated ws %{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://operations.teleautomation.online/ws/mail-monitoring
```

### 13. Removed endpoints, row counts, logs

```bash
for p in /ai/daily-briefing /api/mail-outcome-audit/summary /api/mail-audit-ai/status \
         /bgv/cases /bgv/dashboard /payments/reconciliation /auth/handler-kit ; do
  printf '%-44s %s\n' "$p" \
    "$(curl -s -b /tmp/ops.jar -o /dev/null -w '%{http_code}' \
       "https://operations.teleautomation.online$p")"
done
```

All seven must be **404** while authenticated. A 401 here means the session
expired, not that the route survived — re-authenticate and repeat.

```bash
# row counts must match step 5 exactly, audit tables included
docker exec -i teleautomation-production-operations-db-1 sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' < baseline.sql \
  > /tmp/baseline-after.txt
diff /opt/teleautomation-backups/pre-decommission-$TS/baseline.txt /tmp/baseline-after.txt \
  && echo "IDENTICAL"

# logs: no new critical errors
docker logs --since 30m teleautomation-production-operations-api-1 2>&1 \
  | grep -iE "error|exception|traceback|critical" | head -30
```

Live row counts will drift upward as mail arrives — `mailbox_messages`,
`mail_realtime_events` and `mailbox_sync_jobs` are expected to **grow**. Nothing
may **shrink**, and all ten Mail Audit tables must be **exactly identical**,
including `mail_outcome_audit_approvals = 4`.

Monitor for 30 minutes. Rollback triggers: any 5xx on a retained route, a worker
traceback, a stalled mail queue, or any retained count decreasing.

---

# Rollback

Rollback is cheap because the schema never changed. **No migration to reverse,
no database restore required for this release.**

### R1. Restore the previous image (fast path, ~30 seconds)

The previous images are still on the host, so rollback retags rather than
rebuilds:

```bash
cd /opt/teleautomation
docker tag 962eaf675398 teleautomation-production-operations-api:rollback
docker compose -f docker-compose.production.yml \
               -f teleautomation-messaging/docker-compose.production.yml \
               --env-file .env.production \
  stop operations-api
docker rm -f teleautomation-production-operations-api-1
docker run -d --name teleautomation-production-operations-api-1 \
  --network teleautomation-production_default \
  --env-file /opt/teleautomation/.env.production \
  -p 127.0.0.1:8210:8000 --restart unless-stopped \
  962eaf675398
```

Preferred alternative, staying inside compose — restore the source and rebuild
from the previous commit, which is still on disk:

```bash
mv /opt/teleautomation-business /opt/teleautomation-business.failed-$TS
cp -a /opt/teleautomation/teleautomation-business /opt/teleautomation-business
RELEASE_SHA_OPERATIONS=0207819529e0b6ba88a5e61d435a60108d3557bc \
docker compose -f docker-compose.production.yml \
               -f teleautomation-messaging/docker-compose.production.yml \
               --env-file .env.production \
  up -d --no-deps --force-recreate --build operations-api
```

### R2. Verify the rollback

```bash
curl -s https://operations.teleautomation.online/version   # must be 0207819…
curl -s https://operations.teleautomation.online/health
```

Then: log in, confirm the **twelve-item** sidebar has returned, confirm mail
ingestion still consumes its queue, and re-run the step 5 baseline — retained
counts unchanged, audit tables still at 39,602 rows.

### R3. Database restore — only if independently corrupted

Not required by this release. If ever needed:

```bash
docker exec -i teleautomation-production-operations-db-1 sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < /opt/teleautomation-backups/pre-decommission-$TS/operations.dump
```

### Migrations that cannot be trivially reversed

**None in this release.**

The only irreversible operation in the whole project is
`scripts/decommissioned_audit_tables.py --drop`, which is not part of this
deployment, is not referenced by any runtime module, and would destroy 39,602
rows of audit history including 4 human approval decisions. Restoring those
requires the step 3 dump; there is no down-migration.
