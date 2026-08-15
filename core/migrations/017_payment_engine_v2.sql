-- Payment Engine V2 durable schema for PostgreSQL deployments.
-- The application currently retains its JSON compatibility adapter; this
-- schema provides the uniqueness and transaction target for database rollout.

CREATE TABLE IF NOT EXISTS payment_receiver_accounts (
    id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('COMPANY', 'REFERRER')),
    company_id TEXT,
    referrer_id TEXT,
    account_holder_name TEXT NOT NULL,
    upi_id TEXT,
    normalized_upi_id TEXT,
    bank_account_identifier TEXT,
    payment_phone_number TEXT,
    normalized_payment_phone_number TEXT,
    provider_name TEXT,
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'REJECTED')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    verified_at TIMESTAMPTZ,
    verified_by TEXT,
    history JSONB NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT payment_receiver_account_one_owner CHECK (
        (owner_type = 'COMPANY' AND company_id IS NOT NULL AND referrer_id IS NULL)
        OR
        (owner_type = 'REFERRER' AND referrer_id IS NOT NULL AND company_id IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_receiver_active_upi
    ON payment_receiver_accounts (normalized_upi_id)
    WHERE is_active AND normalized_upi_id IS NOT NULL
      AND normalized_upi_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_receiver_active_bank
    ON payment_receiver_accounts (bank_account_identifier)
    WHERE is_active AND bank_account_identifier IS NOT NULL
      AND bank_account_identifier <> '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_receiver_active_phone
    ON payment_receiver_accounts (normalized_payment_phone_number)
    WHERE is_active AND normalized_payment_phone_number IS NOT NULL
      AND normalized_payment_phone_number <> '';

CREATE TABLE IF NOT EXISTS payment_evidence (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    source_module TEXT NOT NULL,
    source_entity_type TEXT,
    source_entity_id TEXT,
    mime_type TEXT,
    model_name TEXT,
    raw_extraction JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized_extraction JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_evidence_sha256
    ON payment_evidence (sha256);

CREATE TABLE IF NOT EXISTS payment_verifications (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES payment_evidence(id),
    verification_state TEXT NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    receiver_account_id TEXT REFERENCES payment_receiver_accounts(id),
    owner_type TEXT,
    company_id TEXT,
    referrer_id TEXT,
    amount_minor BIGINT,
    currency TEXT NOT NULL DEFAULT 'INR',
    transaction_id TEXT,
    utr TEXT,
    transaction_at TIMESTAMPTZ,
    booking_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key TEXT NOT NULL UNIQUE,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_verification_transaction
    ON payment_verifications (transaction_id)
    WHERE transaction_id IS NOT NULL AND transaction_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_verification_utr
    ON payment_verifications (utr)
    WHERE utr IS NOT NULL AND utr <> '';

CREATE TABLE IF NOT EXISTS payment_ledger_entries (
    id TEXT PRIMARY KEY,
    verification_id TEXT NOT NULL REFERENCES payment_verifications(id),
    transaction_type TEXT NOT NULL,
    gross_amount_minor BIGINT NOT NULL,
    company_share_minor BIGINT NOT NULL DEFAULT 0,
    referrer_share_minor BIGINT NOT NULL DEFAULT 0,
    commission_already_received_minor BIGINT NOT NULL DEFAULT 0,
    company_share_recoverable_minor BIGINT NOT NULL DEFAULT 0,
    total_payout_adjustment_minor BIGINT NOT NULL DEFAULT 0,
    referrer_id TEXT,
    source_module TEXT NOT NULL,
    settlement_status TEXT NOT NULL DEFAULT 'PENDING',
    reversal_of_entry_id TEXT REFERENCES payment_ledger_entries(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_ledger_verification_type
    ON payment_ledger_entries (verification_id, transaction_type)
    WHERE reversal_of_entry_id IS NULL;

CREATE TABLE IF NOT EXISTS payment_entitlements (
    id TEXT PRIMARY KEY,
    verification_id TEXT NOT NULL REFERENCES payment_verifications(id),
    candidate_id TEXT,
    payment_scope TEXT NOT NULL,
    scope_entity_id TEXT,
    is_consumed BOOLEAN NOT NULL DEFAULT FALSE,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (verification_id, payment_scope, scope_entity_id)
);
