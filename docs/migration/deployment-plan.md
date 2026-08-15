# Independent staging and cutover plan

Status: artifacts are staging-ready; no deployment, DNS, TLS, data migration, or production configuration was performed.

## Topology

- Marketing: one API process on loopback port 8100, private Marketing database/data root, Telegram sessions and messaging providers.
- Operations: one API process on loopback port 8200, private Operations PostgreSQL database/data root, recruitment and operational workers.
- Separate environment files, cookies, auth secrets, Linux users, volumes, logs, images, health checks, backups, and rollback targets.
- Public hostnames are deployment inputs. Nginx templates intentionally contain replacement markers.
- Internal traffic uses private URLs plus a shared service credential; /internal/ is not publicly proxied.

## Staging gates

1. Provision isolated databases, data volumes, service users, environment files, and two non-production hostnames.
2. Run Operations migration runner twice; confirm the second run is a no-op and checksums match.
3. Restore only sanitized fixtures. Do not copy Telegram sessions or live user documents into staging.
4. Build immutable images in CI; scan them and record digests.
5. Start Marketing alone and Operations alone; each /health and login flow must work without its peer.
6. Start both and verify cross-project token rejection, idempotent replay, outage queueing/recovery, and dead-letter visibility.
7. Test every role, route family, WebSocket, public slot flow, upload/download, provider sandbox, scheduler, and graceful shutdown.
8. Exercise database/file backup restore and image/config rollback.
9. Compare current-main route/feature inventory with both staged UIs. Resolve mobile ownership separately before calling total platform parity complete.

## Production cutover (requires later explicit authorization)

Take verified backups, record session/file/database checksums without exposing contents, freeze or reconcile writes, migrate Operations data, deploy the exact approved Marketing and Operations image digests, configure private service URLs, then change proxy/DNS/TLS only after both staged releases pass. Monitor queues, workers, callbacks, resource use, and data reconciliation through a defined rollback window.

Rollback stops new writes, retains outbox/post-cutover deltas, restores the prior images/configuration and verified database/file snapshots, then reconciles retained deltas. Never deploy from these working directories.
