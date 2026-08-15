# Staging-readiness validation report

Date: 2026-08-15
Classification: **IN PROGRESS**

This report continues the current-main split from monolith commit
`68a28ecf2301c537eb8ee96f7d30649bd832c2f1`. It records only evidence actually
obtained. Production was out of scope and remained untouched.

## Completion

| Area | Completion |
|---|---:|
| Overall split | 84% |
| Marketing | 93% |
| Operations | 96% |
| Database verification | 82% |
| Container verification | 30% |
| Staging verification | 0% |
| Production readiness | 62% |

Percentages are readiness estimates, not test pass rates. Staging remains zero
because no public staging resource or URL exists.

## Git

| Project | Path | Branch | Initial resync | Validation code | Remote | CI |
|---|---|---|---|---|---|---|
| Marketing | `C:\Users\codet\OneDrive\Desktop\Teleautomation_prod\teleautomation-messaging` | `codex/marketing-current-main-resync-20260815` | `a0371a9f21818c5fb4743909abf66c731f1fa5e1` | `769689b69df2f498708130a7c7ca0c046970a12d` | none | not run |
| Operations | `C:\Users\codet\OneDrive\Desktop\Teleautomation_prod\teleautomation-business` | `codex/operations-current-main-resync-20260815` | `9da24c79561e536e8e0f59c5090bc600304e5eec` | `5c67f951052c4c99932fc3ff04fdf8903235a72a` | none | not run |

Pre-commit inspection found no tracked databases, sessions, uploads, payment
proofs, runtime state, generated build directories, private-key signatures, or
known token signatures. The only environment-named file in either commit is
`.env.example`. Provenance is recorded in each repository.

The authenticated GitHub owner contains the monolith repository and one
unrelated repository. It contains no official Marketing or Operations
repository. No remote was attached, no push occurred, and no GitHub Actions run
ID or artifact exists. Both prepared workflows now include backend tests,
frontend tests/build, `pip check`, a production dependency audit, fresh
PostgreSQL migration, a second-run no-op assertion, Docker build/start, and
health verification. Those workflow definitions are unproven until official
repositories exist.

## Android decision

Classification: **5. INCOMPLETE AND SAFE TO EXCLUDE FROM THIS SPLIT**.

Android is a historical standalone client introduced by monolith commits
described as feature-parity work in progress. It is not imported by either
server, Docker/Compose, deployment scripts, or web/server CI. The wrapper JAR,
JDK 17, Gradle, and Android SDK are unavailable. It is therefore preserved in
Marketing but is not a web/server deployment gate.

A broad `data/` ignore rule was found to hide 29 Android main-source files and
four tests. Exact exceptions now preserve those files. No historical Android
work was deleted. See `android-scope-decision.md`.

## Tests and builds

| Gate | Result |
|---|---|
| Marketing backend | 14 passed, 0 failed, 0 skipped |
| Marketing frontend | 14 passed, 0 failed |
| Marketing production build | passed; 457.00 kB JavaScript |
| Operations backend | 1,403 passed, 0 failed, 1 skipped |
| Operations frontend | 324 passed, 0 failed |
| Operations production build | passed; 610.37 kB JavaScript; non-failing chunk warning |
| Dedicated cross-service outbox/migration tests | 6 passed |
| Internal service-auth tests | 2 passed |
| WebSocket focused tests | 2 passed |
| Local dual-service health/contract/outage run | passed before this phase on isolated ports/data |
| Fresh migration executions | 4 successful runner calls: first and second run for each project |
| Docker tests | 0; no engine |
| Public staging smoke tests | 0; no staging |
| Public staging E2E tests | 0; no staging |

FastAPI emitted only the known `on_event` deprecation warnings. Operations
frontend tests emitted known jsdom `window.focus` not-implemented diagnostics
while still passing. The Operations bundle remains above Vite's 500 kB advisory.

## PostgreSQL proof

A disposable PostgreSQL 16.14 cluster was provisioned on loopback in an isolated
temporary directory. It was not installed as a Windows service.

### Marketing

- Fresh empty database: passed.
- First run: `000_marketing_baseline.sql` applied.
- Second run: zero migrations emitted; clean no-op.
- Final schema: 2 public tables including the migration ledger, 2 indexes, 7
  constraints, zero invalid indexes, zero unvalidated constraints.
- Ledger: 1 checksum-tracked migration.
- Service startup: passed against the fresh database.
- `/health`: HTTP 200; OpenAPI: HTTP 200.
- Direct database smoke: `SELECT 1` passed.

### Operations

- Fresh empty database: passed.
- First run: 27 migrations applied in order from
  `000_operations_baseline.sql` through `026_cross_project_delivery.sql`.
- Collision check: `011_recruitment_mail_gmail_ingestion.sql` and
  `026_cross_project_delivery.sql` both exist in the ledger.
- Second run: zero migrations emitted; clean no-op.
- Final schema: 39 public tables, 114 indexes, 389 constraints, zero invalid
  indexes, zero unvalidated constraints.
- Ledger: 27 checksum-tracked migrations.
- Service startup: passed against the fresh database.
- `/health`: HTTP 200; OpenAPI: HTTP 200.
- Direct database smoke: `SELECT 1` passed.

## Persistence and backup/restore

The source cluster was cleanly stopped and restarted with native `pg_ctl`.
Both schemas, ledgers, and synthetic records survived.

A cold physical backup of test-only data was created with SHA-256
`6c384e9ec5ef51d5f5b960f8e097fe7e411ef9a656b12294ab1ba66d9db35e3a`.
It was restored into a separate directory and started on a different port.
Verification found:

- Marketing synthetic record count: 1; migration ledger count: 1.
- Operations synthetic candidate count: 1; migration ledger count: 27.
- Operations synthetic inbox/outbox relationship count: 1.
- Both services started against the restored databases and returned HTTP 200.

The portable PostgreSQL distribution omits `pg_dump`; therefore this is a cold
physical-backup proof, not a logical dump/restore proof.

## Containers

No Docker, Docker Compose, Podman, containerd/nerdctl, Docker service, or WSL
distribution exists on this workstation. No container image was built and no
Compose service, volume, service-discovery path, or container restart was
executed. The prior process-level dual-service outage/recovery result does not
substitute for Docker Compose. This is a hard staging gate.

## Sanitized split-data rehearsal

**BLOCKED.** Both repositories contain a plan, ownership map, schemas, and
domain-specific compatibility migrations, but no executable end-to-end
monolith-to-two-stores migration tool or reconciliation manifest generator.
No sanitized monolith snapshot was supplied. The synthetic rows used for
database recovery prove persistence only; they do not prove production-data
separation. Implementing and dry-running this tool remains required.

## WebSockets

Ownership is explicit:

- Marketing owns `/ws` and voice signaling WebSockets.
- Operations owns `/ws/mail-monitoring`.

Audit found Marketing `/ws` accepted unauthenticated clients and emitted fleet
state. The endpoint now requires an authenticated admin session before accept,
and a regression test proves rejection code 4403, authenticated state delivery,
and disconnect cleanup.

Operations tests prove unauthenticated rejection, authenticated connection,
ping/pong, missed-event replay, reconnect, and cleanup. TLS/WSS, browser cookies,
cross-origin behavior, and deployed frontend integration remain staging-only
tests.

## Security and dependencies

- Known private-key/token signature scan: zero matches.
- Runtime/database/session/upload/payment-evidence artifacts tracked: zero.
- Service-to-service credentials are environment-only and compared with
  constant-time comparison.
- Unauthenticated internal requests are rejected; command replay is idempotent.
- Marketing production dependency audit: 1 high, 0 critical.
- Operations production dependency audit: 0 vulnerabilities.

Marketing uses `xlsx@0.18.5` in the authenticated Groups Upload browser flow.
The two published advisories cover prototype pollution and ReDoS, no fixed npm
release is available, and the parser is reachable when an admin selects an
untrusted workbook. It is client-side rather than a server parser, so it does
not automatically block isolated staging with trusted fixtures. Production
requires either a maintained replacement or explicit risk acceptance plus file
size/type controls and a trusted-file-only policy. Development-tool audit
findings are separate from the one production advisory.

## Provider verification

| Provider/flow | Status | Evidence |
|---|---|---|
| Telegram accounts/forwarding/campaigns | MANUAL VERIFICATION REQUIRED | code/tests pass; no staging session used |
| CRM/inbox/Marketing AI | MANUAL VERIFICATION REQUIRED | local tests only; no deployed UI/provider run |
| WhatsApp and calls | SAFE TEST NOT AVAILABLE | no isolated provider credentials/callbacks |
| Gmail ingestion/recruitment mail | MANUAL VERIFICATION REQUIRED | fixture tests pass; no staging mailbox |
| OCR/Ollama | MANUAL VERIFICATION REQUIRED | policy/tests pass; no staging OCR node/files |
| Booking/interview workflows | MANUAL VERIFICATION REQUIRED | extensive automated tests; no deployed E2E |
| Payment/evidence flows | SAFE TEST NOT AVAILABLE | automated safe tests only; no real payment action |
| Background workers/schedulers | MANUAL VERIFICATION REQUIRED | safe UI mode intentionally disabled providers |

No provider success is claimed from mocked or fixture-only tests.

## Rollback

A local application rollback proof used detached temporary worktrees at the
immediately previous split commits (`a0371a9…` and `9da24c7…`) against the
restored PostgreSQL data. Both previous services returned HTTP 200 and all
synthetic rows/relationships remained. Temporary rollback worktrees were then
removed.

This is not a staging release rollback: there is no staging release, image
digest, deployment history, or previous hosted revision to roll back.

## Staging URLs

No Marketing staging URL exists.
No Operations staging URL exists.
No custom staging or production domain was created or attached.

Staging deployment was correctly withheld because independent CI, Docker build,
Compose/persistence, and sanitized split-data gates are incomplete.

## Remaining blockers to production cutover

1. Create the two official GitHub repositories, attach correct remotes, push
   through protected branches/PRs, and obtain passing independent CI run IDs.
2. Build both images and pass Docker Compose dual-service, service discovery,
   persistent-volume, outage/recovery, and restart tests.
3. Implement and rehearse the sanitized monolith data/file split with count,
   relationship, ownership, and checksum reconciliation.
4. Provision isolated databases/volumes/secrets and temporary HTTPS staging
   URLs; verify commit/version identity.
5. Run deployed UI, WSS, all four v1 contracts, critical Marketing/Operations
   E2E, provider/manual checks, and hosted rollback.
6. Resolve or formally accept the reachable Marketing `xlsx` risk with
   compensating controls.
7. Obtain explicit production authorization after reviewing staging evidence.

## Production safety confirmation

| Resource | Status |
|---|---|
| `teleautomation.online` | UNCHANGED |
| Production database | UNCHANGED |
| Production DNS | UNCHANGED |
| Production Nginx | UNCHANGED |
| Production PM2/services | UNCHANGED |
| Production Telegram sessions | UNCHANGED |
| Production Gmail state | UNCHANGED |
| Production payment data/evidence | UNCHANGED |
| Production provider callbacks | UNCHANGED |

No push, merge, public deployment, service restart, or production configuration
change occurred.
