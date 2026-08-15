-- A blocked automatic booking must say why, in the notification row itself.
--
-- The validator's failure code was previously kept only on the booking audit,
-- so the Mail Monitoring table could show "Automatic Booking Blocked" and
-- nothing else. These columns carry the decision to the row an operator reads.

ALTER TABLE mail_monitoring_notifications
  ADD COLUMN IF NOT EXISTS booking_block_reason_code text;
ALTER TABLE mail_monitoring_notifications
  ADD COLUMN IF NOT EXISTS booking_block_reason text;
-- The exact validator branch, kept alongside the operator-facing reason so a
-- cause the mapping deliberately merges can still be identified.
ALTER TABLE mail_monitoring_notifications
  ADD COLUMN IF NOT EXISTS booking_failure_code text;
