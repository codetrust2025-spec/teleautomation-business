# Cross-project contracts (current-main resync)

Status: implemented locally; staging outage/replay verification is still required.

Marketing and Operations share no Python imports, writable directories, databases, or provider clients. Every call uses INTERNAL_SERVICE_TOKEN in X-Internal-Service-Token; command endpoints also require X-Idempotency-Key. Public Nginx templates return 404 for /internal/.

## Operations to Marketing

- GET /internal/v1/operational-summary?stale_days=N
  - Read-only, bounded CRM projection used by the Operations daily briefing.
  - Response: status, generated_at, followups_due[], stale_leads[].
- POST /internal/v1/notifications
  - Body: title, body, tag, optional whatsapp_text.
  - Marketing owns Web Push and WhatsApp delivery.
  - Operations persists marketing.notification.v1 before delivery.

## Marketing to Operations

- POST /internal/v1/opportunities
  - Body: Marketing conversation identity plus bounded opportunity/contact snapshot.
  - Operations owns the resulting Data Room record.
  - Marketing persists operations.opportunity.v1 before delivery.
- POST /internal/v1/candidates/{candidate_id}/payment-proofs
  - Authenticated multipart upload: file, note, source_module, upload_context.
  - Operations owns validation and storage. The stable content/context key makes retries idempotent.

## Reliability

JSON outboxes live under each project's private data directory. Enqueue precedes network I/O. Dispatch happens immediately and every 30 seconds, with exponential backoff capped at one hour, 12 attempts, and a retained dead state for operator reconciliation. Consumers reserve idempotency keys and release the reservation if processing raises, so failed work can retry. A duplicate receives a stable duplicate response.

Service startup and /health never require the peer. Missing peer URLs/tokens leave events pending rather than failing startup. Read-only daily-summary calls fail closed to no peer data.

Required staging proof: token rejection, duplicate replay, peer outage and recovery, dead-letter inspection, malformed request rejection, payment-proof retry, and confirmation that neither service can read the other's database or data root.
