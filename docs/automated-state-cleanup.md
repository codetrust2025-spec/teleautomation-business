# Automatic mail decisions: release RCA

Baseline inspected: Operations `61be0d4`, 11 September 2026. Production checks
and source-corpus inference were read-only; no historical replay or repair was
requested by this release.

Rebased on verified production `01232c2`. Retained its all-booking-row read,
source-supported cancellation handling, original HTML email display and clock
test fixes. The user explicitly confirmed that identical start/end times are
allowed for different interviews. Removed the upstream schedule-only duplicate
fallback: exact times, company names and job titles alone are not event proof.
Source-proven Teams siblings work in either arrival order (plain mail or ICS
first); conflicting non-empty UIDs stay independent. No data is rewritten.
Booking reads remain uncollapsed but require persisted identity links or exact
phone/email/explicit profile identity, not name equality. Display grouping keeps
its compatibility default. Missing thread metadata uses source-message identity; legacy
unthreaded tombstones remain readable only for that exact source message.

Read-only inspection of Persistent Systems' invitation/reminder found matching
public job URLs and account identity, but no shared unique event token. These
are not sufficient on their own to infer lifecycle identity. The former
schedule-only regression fixture now explicitly rejects that inference; a
future provider-specific matcher must establish event identity, not merely
restore time equality as a veto. No historical mail was reprocessed.

## Operational review exits

Some validator returns still emitted manual-review fields. The worker's retry
branch only recognizes AI_RETRY_PENDING, so these returns could reach the ignore
branch. Normalize every public validator return and the worker boundary, keeping
unverified evidence unverified. Unsupported quotes, uncorroborated proposals,
unquoted source transitions, low confidence, unresolved disagreement and model
outages stay retryable. A source-entailed, high-confidence reading with no risk
flags no longer depends on an obsolete model UI boolean.

The non-calendar relevance path also silently ignored UNKNOWN, low-confidence
and unsupported relevance answers. These now retry. Clear, sufficiently
confident, source-supported non-recruitment answers remain ignored.

The existing leased worker handles retries with exponential backoff (2 minutes
initially, capped at 256 minutes by the current exponent cap). No new polling
loop, manual approval step or bulk historical recovery was added.

The old approval component and notification correction/rerun actions are
retired. Existing read/unread/dismiss actions and evidence history remain.
Historical model schemas and stored audit vocabulary remain readable for
compatibility; they are not operational human-review destinations. Migration
032 only extends the lifecycle constraint to accept AI_RETRY_PENDING.

## Risebird

Both real Mphasis/Risebird mails contain a candidate interview date/time,
an Interview Meeting Link, and a Job Description link. The unconditional
job-description regex classified them as advertisements before source
validation. Exempt only this phrase when a concrete scheduled interview
invitation is present. Other vacancy, portal, advertisement and webinar gates
remain. The patch does not approve a mail merely because Risebird sent it.

## HCLTech sibling

Booked source `1a0800b8af2b5cb7` and sibling `1a0800caefa4a653` share subject,
11 September 12:00–12:45 IST schedule, and the same source Teams meeting URL.
The sibling has a different Gmail thread and no ICS attachment. One slot
`27338c82ac` exists. Previously the sibling was ignored by classification;
that was NOT proof the duplicate gate worked.

Use exact source Teams meeting identity + identical subject + exact schedule
within the existing candidate scope for the no-UID sibling. Raw MIME source is
read from storage, not model-supplied URLs. Different non-empty calendar UIDs
remain different interviews even if their times overlap. A failed proof read
fails closed rather than permitting a duplicate. The real executor is tested
with slot creation forbidden.

## Reconciliation, no data repairs

The complete pre-release snapshot contains 33 findings: 27 canonical-ID drift,
two deactivated bookings without cancellation evidence, two stored booked
alerts for inactive slots, one legacy AI-marked slot without audit, and one
applied lifecycle without its confirmed slot. Seven cancellation histories
are separately explained by the engine.

Of the 27 ID findings, 25 resolve to the event's canonical ID using persisted
identity links. Two have split identity roots: `1cadd73985` resolves to
`19f1f2575e` rather than event root `7a6a68d247`; `5e6e726101` resolves to
`21d8b5d043` rather than event root `d6a32e4b50`. All ten distinct alias/root
pairs have matching stored names and phones. This is evidence for an eventual
audited reference normalization, not permission to rewrite historical rows.

The new applied-lifecycle mismatch has stronger evidence: cancellation source
`1a05bbeb9ab95505`, sent 1 September, was reprocessed at 02:07 UTC on
11 September against booking `a083c2166c`, whose confirmed source was sent
10 September. The cancellation had no UID; its fallback lifecycle key missed
the calendar lifecycle, and the target selector accepted the surviving slot.
The target's persisted source send-time is now an additional precedence guard
for no-UID updates/cancellations. Matching UID/SEQUENCE remains authoritative.
The already affected slot has NOT been restored; that needs a separately
approved, fully revalidated repair.

Legacy slot `890e1e11e8` is Shailaja's 1 September slot, not a new booking made
by these checks. It remains untouched pending provenance inspection. The 2027
slot and all historical records remain unchanged by this release.

## Node comparison limitations

Both endpoints reported Ollama 0.34.0 and identical qwen2.5:7b Q4_K_M digest
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
Effective request keep_alive is 5m; the environment value is blank. Both
reported context length 4096. No seed, context or routing changes were made.

RTX4060 began cold, loaded the text model with size_vram=0, took 41.825s for
relevance (7.319s load), then timed out at the 90s diagnostic classification
limit. The probe stopped adding work to that node. This is a CPU-residency
measurement, NOT a valid comparison of two GPUs.

Jagadeesh stayed resident with 4,325,281,627 bytes in VRAM. Across two repeats
of three real mails, high-level verdicts repeated, but output hashes differed.
Risebird primary and validator both read INTERVIEW_CONFIRMED, while the old
backend rejected JOB_ADVERTISEMENT. HCLTech was misread as GENERAL at 0.5
confidence in this non-calendar analysis path (the live calendar path had
already booked it). The public workshop was correctly rejected both times.
Observed total request latency includes shared-node queue time, so it is not
an isolated throughput benchmark.

No canonical text-node winner is established by this small, hardware-confounded
sample. No forced model unload or vision eviction test was performed against
the active production worker. Next: obtain RTX4060 host GPU/runtime diagnostics
and an isolated node window, then repeat the labeled corpus with warm/reload
and text/vision co-residency controls. Keep current routing meanwhile.
Ollama's documented keep_alive and concurrent-model behavior is described at
https://docs.ollama.com/faq; keep_alive alone is not proof of GPU residency.
