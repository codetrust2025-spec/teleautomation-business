# Current-main split resync report

Date: 2026-08-15
Final classification: **IN PROGRESS**

This is the authoritative report for the 2026-08-15 resync. Older July extraction reports and generated inventories are historical evidence only.

## 1. Source of truth

| Item | Evidence |
|---|---|
| Monolith | TelegramForward |
| Branch | main at inspection; safety work on codex/split-current-main-resync-20260815 |
| Authoritative SHA | 68a28ecf2301c537eb8ee96f7d30649bd832c2f1 |
| Worktree at checkpoint | clean; local main matched origin/main |
| Historical drift | 387 commits after the July split baseline |
| Split index checkpoints | Marketing ab77fec2e94f27d1f4749ffc9e0676549b140002; Operations c07dbace622e1f18e4c9a1855dc153e419d46927 |

## 2. Marketing

Completion: **91%**

Current Marketing backend/frontend behavior was resynced: Telegram accounts/sessions, groups, campaign and forwarding control, inbox/CRM, WhatsApp, calls/voice, Web Push, Marketing AI, knowledge assistant, admin/fleet monitoring, workers/schedulers, persistence, auth, WebSocket, and notification sounds. Operations pages/routes and handler identity were removed. Current code exposes 153 registered routes and 117 OpenAPI paths.

Marketing uses its own cookie, auth secret, data root, optional PostgreSQL schema, Docker volume, image, Compose service, CI, and configurable Operations URLs. Its opportunity handoff uses a typed v1 endpoint, service token, idempotency key, durable outbox, retry/backoff, dead-letter state, and consumer deduplication.

Outstanding: Android is an incomplete historical scaffold (missing source packages and wrapper/toolchain) and cannot be claimed current-main compatible. The Docker/Compose job and fresh PostgreSQL migration are prepared in CI but could not be executed on this workstation. Provider-backed Telegram/WhatsApp/call flows need sanitized staging credentials.

## 3. Operations

Completion: **95%**

Operations was deeply resynced from current main: candidates, lifecycle, public slots, booking, interview scheduling/reconciliation/attendance, recruitment Gmail ingestion and queues, mail review/audit, OCR gates, payment engine/evidence/allocation/duplicate protection/reconciliation, entitlements/ledger, BGV, handlers/referrers/staff, expenses/salaries, Data Room, reporting, notifications, uploads, workers and schedulers. The standalone API exposes 179 registered routes and 144 OpenAPI paths.

The obsolete split-only 011 outbox migration was removed. The current 011 Gmail migration remains canonical; 026 owns cross-project delivery tables. A checksum ledger, 000 candidates baseline, and deterministic 000-026 chain are present.

Outstanding: fresh PostgreSQL execution and Docker/Compose startup are configured in CI but were not locally executable. Gmail/OCR/payment/provider flows and backup/restore need sanitized staging verification.

## 4. Feature parity matrix

| Feature | Current main | Marketing | Operations | Shared contract | Verified |
|---|---:|---:|---:|---:|---|
| Telegram accounts/sessions/groups | yes | owner | excluded | no | code/tests/build |
| Campaigns/forwarding/fleet | yes | owner | excluded | no | code/tests/build |
| Inbox/CRM/lead follow-up | yes | owner | excluded | bounded summary | code/tests/build |
| WhatsApp/calls/voice/Web Push | yes | owner | excluded | notification command | code/tests/build |
| Marketing AI/knowledge/admin | yes | owner | excluded | opportunity event | code/tests/build |
| Candidates/lifecycle/resumes | yes | excluded | owner | payment-proof upload | 1,400+ suite |
| Public slots/booking/interviews | yes | excluded | owner | notification command | 1,400+ suite |
| Attendance/reconciliation | yes | excluded | owner | no | 1,400+ suite |
| Gmail ingestion/queues/mail audit | yes | excluded | owner | no | 1,400+ suite |
| OCR and AI processing status | yes | excluded | owner | no | policy/tests/build |
| Payments/evidence/allocation/duplicates | yes | excluded | owner | proof upload | 1,400+ suite |
| Entitlements/ledger/reconciliation | yes | excluded | owner | no | 1,400+ suite |
| BGV | yes | excluded | owner | no | backend/frontend suite |
| Handlers/referrers/staff | yes | excluded | owner | no | auth/scope tests |
| Expenses/salaries | yes | excluded | owner | no | backend/frontend suite |
| Data Room/opportunities | yes | producer only | owner | opportunity event | dual-service test |
| Daily operational briefing | yes | bounded CRM projection | owner | summary GET | dual-service test |
| Authentication/roles | yes | admin-only | admin/handler | service token | focused + dual test |
| WebSockets | yes | Marketing /ws | Operations mail WS | no | source/tests; staging pending |
| Native Android | yes | incomplete scaffold | absent | decision required | not verified |

## 5. Database and persisted-data matrix

| Table/data store | Future owner | Migration ready | Verified |
|---|---|---:|---|
| ai_smart_reply_store | Marketing PostgreSQL | yes, migration 000 | local execution unavailable |
| Marketing accounts/groups/message/CRM/inbox/call/push JSON and media | Marketing data volume | ownership mapped | unit/local API |
| Telegram session files | Marketing private volume | cutover plan only | live files untouched |
| candidates_store | Operations PostgreSQL/compatibility store | yes, baseline 000 | tests; fresh PG pending |
| Recruitment/Gmail/mail audit tables (001-025) | Operations PostgreSQL | yes | tests; fresh PG pending |
| payment_evidence, payment_verifications, payment_ledger_entries, payment_entitlements, receiver accounts | Operations PostgreSQL | yes, migration 017 | tests; fresh PG pending |
| Candidate resumes/proofs/payment evidence/OCR files | Operations data volume | ownership mapped | unit tests; staging pending |
| Data Room/expenses/salaries/BGV/referrer/staff/reminder JSON | Operations data volume | ownership mapped | unit tests |
| cross_project_inbox/outbox SQL tables | Operations database | yes, migration 026 | SQL execution pending |
| Runtime JSON cross-project outbox/inbox | each producer/consumer data volume | yes | unit + outage/recovery |

No application imports the peer, reads the peer database, or shares a writable volume.

## 6. Migration results

Marketing has a checksum-tracked 000 baseline for its optional PostgreSQL table. Operations has one migration per numeric version, with 011 assigned to Gmail ingestion and 026 assigned to cross-project delivery. Migration uniqueness tests pass.

Exact fresh PostgreSQL result: **NOT RUN LOCALLY** because docker and psql are unavailable. Both CI workflows provision PostgreSQL 16 and execute the project migration runner before tests. This unverified external gate prevents SOURCE PARITY COMPLETE or STAGING READY classification.

## 7. Cross-service contracts

1. Operations to Marketing: GET /internal/v1/operational-summary.
2. Operations to Marketing: POST /internal/v1/notifications.
3. Marketing to Operations: POST /internal/v1/opportunities.
4. Marketing to Operations: POST /internal/v1/candidates/{candidate_id}/payment-proofs.

Command bodies are bounded Pydantic schemas where JSON is used. Authentication uses X-Internal-Service-Token; mutations require X-Idempotency-Key. JSON events use versioned event names. Producer outboxes retry for 12 attempts with exponential backoff capped at one hour and retain dead failures. Consumer claims are released on failed processing.

## 8. Test results

| Gate | Result |
|---|---|
| Marketing backend | 13 passed, 0 failed, 0 skipped |
| Marketing frontend | 14 passed, 0 failed |
| Marketing production build | pass; 457.00 kB JS |
| Operations backend | 1,402 passed, 0 failed, 1 skipped |
| Operations frontend | 324 passed, 0 failed |
| Operations production build | pass; 610.37 kB JS, non-failing chunk-size warning |
| Contract/auth/outbox focused tests | pass |
| Local dual-service health/contracts | pass on 8100/8200 with isolated temporary data |
| Peer-down recovery | pass in both directions; pending attempt recovered to delivered |
| Fresh PostgreSQL | not run locally; CI prepared |
| Docker/Compose | not run locally; CI prepared |
| Staging | not deployed |

Dual-service testing used safe UI mode, fixture credentials, and temporary isolated data roots. It verified health, 401 rejection, authenticated success, duplicate replay, peer outage, and recovery. It did not start real providers or background workers.

## 9. Git status

Both output repositories currently have no commits and no remotes. They contain reviewable staged implementation work and must not be pushed until repository destinations are supplied. Provenance is recorded in PROVENANCE.md. Runtime/generated files are excluded.

## 10. Staging

No staging URL, database, or deployed SHA exists. No custom domain is configured. Temporary URLs are therefore **not available**. Staging is blocked on remote/repository ownership, a Docker-capable CI run, fresh migration evidence, staging infrastructure, sanitized provider fixtures, and the Android ownership decision.

## 11. Remaining production work

1. Resolve Android ownership and restore/build/test current-main mobile parity or explicitly scope mobile out of this cutover.
2. Create/attach the two Git remotes, review staged inventories, commit on protected feature branches, and run CI.
3. Obtain passing PostgreSQL migration-twice, Docker image, Compose health, dependency/image scan, and artifact digest evidence.
4. Provision isolated staging services/databases/volumes and temporary TLS URLs.
5. Run real role, WebSocket, provider sandbox, upload, worker/scheduler, backup/restore, rollback, and cross-service outage/reconciliation tests.
6. Rehearse zero-data-loss migration using sanitized snapshots and verify counts/checksums/references.
7. Only after explicit authorization: take production backups, perform cutover, then configure final domains/callbacks.

## 12. Production safety

| Target | Touched |
|---|---|
| teleautomation.online | NO |
| Production database/schema/data | NO |
| Production DNS/TLS | NO |
| Production Nginx | NO |
| Production PM2/services/processes | NO |
| Live Telegram sessions | NO |
| Live Gmail/payment evidence/uploads/runtime state | NO |
| Push/merge/deploy | NO |

## 13. Final classification

**IN PROGRESS**

Server and web parity work is strong and local dual-service contracts recover from peer outages. The split is not complete under the requested definition because fresh database/container CI evidence, native Android parity, staging deployment, provider E2E, and rollback rehearsal are still outstanding.
