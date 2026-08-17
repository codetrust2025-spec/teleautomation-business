# Six-feature decommission

Daily Briefing, Mail Audit, Payment Reconciliation, BGV Register, Handler Kit
and Settings were removed from Operations on 2026-08-17. Operations now ships
six features: Daily Ops, Candidates, Slot Booking, Mail Alerts, Data Room and
AI Mail Review.

Removal means removal: each feature lost its panel, its view id, its backend
routes, its modules and its background work in the same change. Nothing is
hidden behind a flag or an unlinked route.

## What each feature owned, and what it only borrowed

| Feature | Frontend | Backend | Jobs | Storage |
|---|---|---|---|---|
| Daily Briefing | `DailyBriefingCard.jsx`, `dailyBriefing.css` | `/ai/daily-briefing`, `/ai/daily-briefing/refresh`, `core/daily_briefing.py` | `operations-daily-briefing` scheduler task | `data/daily_briefings.json` cache |
| Mail Audit | `OutcomeAuditPanel.jsx`, `outcomeAudit.css`, `MailMonitoringTabs.jsx` | 17 routes (`/api/mail-outcome-audit/*`, `/api/mail-audit-ai/*`), `recruitment_mail_audit{,_store}.py`, `recruitment_audit_ai.py` | two worker loops inside the mail worker | 10 Postgres tables |
| Payment Reconciliation | `PaymentReconciliationPanel.jsx` | `/payments/reconciliation`, `/payments/reconciliation.csv`, `features/payment_reconciliation.py` | none | none — computed from candidate data |
| BGV Register | `BgvRegisterPanel.jsx` | 8 `/bgv/*` routes, `features/bgv_register.py` | none | `data/bgv_register.json` |
| Handler Kit | `HandlerKitPanel.jsx` | `/auth/handler-kit`, `handler_kit_for()` | none | none — read-only view of Data Room credentials |
| Settings | `OcrPolicyPanel.jsx` | none of its own | none | none |

Daily Briefing turned out to own nothing but its cache: it aggregated
`candidate_store` and a Marketing CRM projection, both of which are untouched.

## Shared dependencies that were deliberately kept

These carry the name of a removed feature but belong to a retained one. Each
was traced to a live caller before being kept.

| Kept | Why |
|---|---|
| `/api/mail-monitoring/booking-audit` | "audit" in the name, but it reads `recruitment_mail_store.list_booking_audit` and is called by **Mail Alerts** |
| `/handler-expenses/*`, `/handler-salaries/*`, `/company-expenses/*` | "handler" in the name, but these are **Candidates** payout and expenditure flows, not Handler Kit |
| `features/financial_reconciliation.py` | duplicate-transaction detection for Candidates' handler expenses |
| `features/payment_recalculation_audit.py`, `PAYMENT_RECALCULATION_AUDIT_FILE` | written by `candidate_store` when a candidate's payments are recalculated |
| `candidate_store.payment_reconciliation_gap` | a **candidate** field surfaced by payment receipts |
| BGV amounts in `features/payment_allocation.py` | BGV is a financial category in the allocation engine, independent of the BGV Register page |
| `AI_MAIL_RECONCILIATION_MAX_MESSAGES` | caps a Gmail **ingestion** backfill |
| `mailbox_messages.reply_to_email`, `.return_path_email` | added by an audit migration, but written by retained Gmail ingestion |
| `features/data_room_credentials_store.py` | Handler Kit read from it; **Data Room** owns it |
| `core/ocr_policy.py` and `/ai/ocr-policy*` | OCR survives Settings — see below |

## OCR

The standalone Settings page is gone. The project-wide OCR switch moved into AI
Mail Review as `components/OcrToggle.jsx`, in the page header beside Refresh.

It reuses the existing mechanism unchanged: `GET /ai/ocr-policy` to read,
`PUT /ai/ocr-policy` to write, admin-only on the server and disabled on the
client for non-admins, with the same spelled-out confirmation about Tesseract
stopping everywhere. There is no new settings screen, no status section and no
second control.

`GET /ai/ocr-policy/audit` is intentionally retained and now has no UI caller.
It is the change record for a security-relevant switch; `ocr_policy` keeps
writing to it whether or not anything reads it.

While moving the control, a real defect was found and fixed. `OcrPolicyPanel`
did `const confirm = useConfirm()` and then called `confirm(...)`, but the
provider supplies `{ confirm }` — so the call threw and the toggle did nothing.
Its test passed because the mock returned a bare function. `OcrToggle` uses the
provider's real shape and its test mocks that shape.

## Change password

`ChangePasswordModal` was reachable only from Handler Kit. Rather than lose an
auth capability with an unrelated page, it moved to the account menu in the
sidebar and header, next to Sign out. No new page, route or sidebar entry.

## Database

No migration was added. The schema is unchanged by this release, so deploying
it performs no DDL at all.

Mail Audit exclusively owns ten tables. They are now inert — zero runtime
modules reference them — and are **retained by default** because they hold
audit findings and human approval decisions.

```
mail_outcome_audit_runs              mail_outcome_audit_findings
mail_outcome_audit_finding_history   mail_outcome_audit_candidates
mail_outcome_audit_gaps              mail_outcome_audit_approvals
mail_outcome_audit_cleanup_log       mail_audit_ai_queue
mail_audit_ai_results                mail_audit_ai_log
```

Their foreign keys point outward, into `candidate_mailboxes` and
`mailbox_messages`; nothing points inward. Dropping them orphans nothing, but
`scripts/decommissioned_audit_tables.py` re-proves that against the live schema
rather than trusting this note.

A drop is **not** part of any migration. `core/migrations/runner.py` applies
every `NNN_*.sql` automatically at startup, so a drop migration would destroy
audit history as a side effect of a deploy. The script therefore lives outside
that directory and refuses to run without `--drop`, `--confirm-database` and
`--i-have-a-verified-backup`.

```bash
python -m scripts.decommissioned_audit_tables            # counts + FK proof
```

Migrations `019`–`022` remain applied and their checksums unchanged; the runner
rejects edits to applied migrations, so their history stays intact.

### Retained data

| Object | Reason |
|---|---|
| 10 Mail Audit tables | historical findings and human approval decisions |
| `mailbox_messages.reply_to_email`, `.return_path_email` | written by retained Gmail ingestion |
| `data/bgv_register.json` | BGV collections and settlements; financial history |
| `data/daily_briefings.json` | cache only, harmless; left rather than touching production data |

Runtime data files are not in this repository and were not modified.

## Known compatibility risk, outside this repository

Marketing's `core/business_compatibility.py` proxies a prefix list to
Operations that still includes `ai/daily-briefing` (and `api`, which covered the
audit routes). Those paths will now return 404 through the bridge instead of
being served. Nothing calls them — the UI that did is gone — so this is stale
configuration rather than a broken flow.

Fixing it requires a change to `codetrust2025-spec/teleautomation-messaging`,
which is out of scope here. Proposed removal: drop `"ai/daily-briefing"` from
`PREFIXES` in the next Marketing release.

## Remaining dead code, not introduced here

Twenty frontend modules are unreachable from `main.jsx`; all twenty predate this
change and none were orphaned by it. They are mostly the Marketing notification
sounds the split copied in. Twenty-three backend routes have no UI caller, down
from thirty-six before this change, and every remaining one belongs to a
retained feature. See `dashboard/src/featureReachability.test.js`.
