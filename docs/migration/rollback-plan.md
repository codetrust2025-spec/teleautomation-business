# Rollback plan

Status: **unverified; no rollback claim is made**

Rollback must independently cover application releases, workers, schedulers, database compatibility, file locations, Nginx routing, OAuth/webhook compatibility, and data written after cutover.

```mermaid
flowchart TD
    Trigger[Rollback criterion met] --> StopWrites[Stop new writes or queue intake]
    StopWrites --> Preserve[Preserve post-cutover outbox, inbox, DB, and file deltas]
    Preserve --> Proxy[Restore prior Nginx compatibility routing]
    Proxy --> Release[Restore prior application releases]
    Release --> Data[Restore or reconcile compatible data]
    Data --> Workers[Restart prior workers and schedulers safely]
    Workers --> Verify[Health, contracts, queues, callbacks, checksums]
    Verify --> Reconcile[Reconcile operations written during cutover]
```

Rollback decision criteria, maximum downtime, and delta-reconciliation rules must be agreed before production authorization. The complete procedure must be tested against staging copies before it can be described as supported.
