-- Migration 009: Allow multiple Gmail mailboxes per candidate
-- Drops the UNIQUE(candidate_id, provider) constraint and replaces it
-- with UNIQUE(candidate_id, lower(email_address)) so each unique email
-- address is its own row, enabling one candidate to have multiple Gmails.

-- Step 1: Drop the old unique constraint
ALTER TABLE candidate_mailboxes DROP CONSTRAINT IF EXISTS candidate_mailboxes_candidate_id_provider_key;

-- Step 2: Add new unique constraint on (candidate_id, email)
-- Use a unique index with lower() so comparison is case-insensitive.
CREATE UNIQUE INDEX IF NOT EXISTS candidate_mailboxes_candidate_email_key
  ON candidate_mailboxes (candidate_id, lower(email_address));
