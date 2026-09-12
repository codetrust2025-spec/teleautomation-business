-- Preserve every existing row byte-for-byte in its original columns. New
-- outcome facts append; a retry returns its existing fact rather than UPDATE.
ALTER TABLE interview_auto_booking_audit
  ADD COLUMN audit_fact_key text,
  ADD COLUMN source_event_id text,
  ADD COLUMN lifecycle_transition_key text,
  ADD COLUMN source_snapshot jsonb;
ALTER TABLE interview_auto_booking_audit DROP CONSTRAINT
  interview_auto_booking_audit_gmail_message_id_classificatio_key;
CREATE UNIQUE INDEX interview_booking_audit_fact_key
  ON interview_auto_booking_audit(audit_fact_key);
CREATE INDEX interview_booking_audit_message_history
  ON interview_auto_booking_audit(gmail_message_id,classification,created_at DESC);

CREATE FUNCTION reject_booking_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.audit_fact_key IS NULL OR NEW.audit_fact_key = '' THEN
      RAISE EXCEPTION 'New booking audit facts require an idempotency key'
        USING ERRCODE = '23514';
    END IF;
    IF NEW.auto_booked AND NULLIF(NEW.booking_id,'') IS NULL THEN
      RAISE EXCEPTION 'Successful booking audit facts require a booking reference'
        USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Booking audit history is append-only; append a new outcome fact'
    USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER booking_audit_immutable_rows
  BEFORE INSERT OR UPDATE OR DELETE ON interview_auto_booking_audit
  FOR EACH ROW EXECUTE FUNCTION reject_booking_audit_mutation();
CREATE TRIGGER booking_audit_no_truncate
  BEFORE TRUNCATE ON interview_auto_booking_audit
  FOR EACH STATEMENT EXECUTE FUNCTION reject_booking_audit_mutation();
