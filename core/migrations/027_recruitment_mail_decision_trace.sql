-- Persist each decision layer independently so model conclusions never obscure
-- deterministic context or the backend's final fail-closed disposition.

ALTER TABLE mail_ai_analyses
  ADD COLUMN IF NOT EXISTS deterministic_context jsonb,
  ADD COLUMN IF NOT EXISTS recruitment_relevance_result jsonb,
  ADD COLUMN IF NOT EXISTS primary_model_result jsonb,
  ADD COLUMN IF NOT EXISTS validator_model_result jsonb,
  ADD COLUMN IF NOT EXISTS reconciled_result jsonb,
  ADD COLUMN IF NOT EXISTS backend_validated_final_result jsonb;
