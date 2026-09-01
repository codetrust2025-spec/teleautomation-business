-- Employee attendance, working-day calendar, and salary-eligibility audit.
-- Runtime office-network values and credentials are deliberately not stored here.

CREATE TABLE IF NOT EXISTS operations_attendance_policy (
    policy_key TEXT PRIMARY KEY,
    effective_date DATE NOT NULL,
    business_timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    attendance_start_time TIME NOT NULL DEFAULT TIME '09:00',
    updated_by_account_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO operations_attendance_policy (
    policy_key,
    effective_date,
    business_timezone,
    attendance_start_time
) VALUES (
    'default',
    (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date,
    'Asia/Kolkata',
    TIME '09:00'
) ON CONFLICT (policy_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS operations_public_holidays (
    holiday_date DATE PRIMARY KEY,
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_account_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_account_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_operations_public_holidays_active_date
    ON operations_public_holidays (holiday_date) WHERE active;

CREATE TABLE IF NOT EXISTS operations_attendance_records (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    attendance_date DATE NOT NULL,
    marked_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('VERIFIED')),
    office_network_verified BOOLEAN NOT NULL CHECK (office_network_verified),
    network_verification JSONB NOT NULL DEFAULT '{}'::jsonb,
    audit_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, attendance_date)
);

CREATE INDEX IF NOT EXISTS idx_operations_attendance_date_account
    ON operations_attendance_records (attendance_date, account_id);

CREATE TABLE IF NOT EXISTS operations_auth_activity (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    activity_type TEXT NOT NULL CHECK (activity_type IN ('LOGIN', 'LOGOUT')),
    session_id_hash TEXT,
    happened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_operations_auth_activity_account_time
    ON operations_auth_activity (account_id, happened_at DESC);

CREATE TABLE IF NOT EXISTS operations_earnings_eligibility_state (
    account_id TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    salary_reference TEXT NOT NULL,
    attended_working_days INTEGER NOT NULL CHECK (attended_working_days >= 0),
    required_working_days INTEGER NOT NULL CHECK (required_working_days >= 0),
    attendance_ratio NUMERIC(7, 4),
    eligibility_amount INTEGER NOT NULL CHECK (eligibility_amount IN (15000, 40000)),
    calculation_reason TEXT NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, period_start)
);

CREATE TABLE IF NOT EXISTS operations_earnings_eligibility_events (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    salary_reference TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    previous_eligibility_amount INTEGER,
    new_eligibility_amount INTEGER NOT NULL CHECK (new_eligibility_amount IN (15000, 40000)),
    attended_working_days INTEGER NOT NULL,
    required_working_days INTEGER NOT NULL,
    attendance_ratio NUMERIC(7, 4),
    reason TEXT NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_operations_eligibility_events_account_period
    ON operations_earnings_eligibility_events (account_id, period_start, period_end, calculated_at DESC);

CREATE TABLE IF NOT EXISTS operations_salary_change_recommendations (
    id TEXT PRIMARY KEY,
    eligibility_event_id TEXT NOT NULL UNIQUE REFERENCES operations_earnings_eligibility_events(id),
    account_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    salary_reference TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    previous_eligibility_amount INTEGER NOT NULL,
    recommended_eligibility_amount INTEGER NOT NULL CHECK (recommended_eligibility_amount IN (15000, 40000)),
    attendance_ratio NUMERIC(7, 4),
    attended_working_days INTEGER NOT NULL,
    required_working_days INTEGER NOT NULL,
    reason TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'APPROVED_PENDING_APPLY', 'APPLIED', 'REJECTED', 'SUPERSEDED', 'APPLY_FAILED')),
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by_account_id TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    salary_amount_before INTEGER,
    salary_amount_after INTEGER,
    applied_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_operations_salary_recommendations_status_time
    ON operations_salary_change_recommendations (review_status, calculated_at DESC);
