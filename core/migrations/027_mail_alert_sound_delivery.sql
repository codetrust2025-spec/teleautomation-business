-- An alert an operator never hears is an alert that did not arrive.
--
-- The sound is played by the browser when a `notification_created` real-time
-- event reaches it, so the event -- not the alert row -- is what delivery
-- means. Publishing is best effort and its failures were logged at debug and
-- otherwise swallowed, which is how a backfill could fill the Mail Alerts
-- screen in total silence with nothing recording that it had.
--
-- These columns make that state answerable per alert: which real-time event
-- carried it, when, and whether it was carried at all.
ALTER TABLE mail_monitoring_notifications
  ADD COLUMN IF NOT EXISTS sound_delivery_status text NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE mail_monitoring_notifications
  ADD COLUMN IF NOT EXISTS sound_delivered_at timestamptz;
ALTER TABLE mail_monitoring_notifications
  ADD COLUMN IF NOT EXISTS sound_delivery_event_id text;

-- The verifier looks up alerts by the event that announced them; without this
-- the lookup is a sequential scan of every real-time event ever published.
CREATE INDEX IF NOT EXISTS idx_mail_realtime_notification
  ON mail_realtime_events(notification_id, event_type);

-- Finding the alerts that still owe a sound must not scan the whole table.
CREATE INDEX IF NOT EXISTS idx_mail_notifications_sound_pending
  ON mail_monitoring_notifications(created_at DESC)
  WHERE sound_delivery_status <> 'DELIVERED';
