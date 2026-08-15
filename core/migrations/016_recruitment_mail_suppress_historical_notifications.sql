-- Past interviews discovered by a historical mailbox scan are audit records,
-- not actionable notifications. Preserve all evidence and booking audit links
-- while removing legacy rows from unread counts and user-facing alert queues.
UPDATE mail_monitoring_notifications
SET dismissed_at = COALESCE(dismissed_at, now()),
    is_read = true,
    read_at = COALESCE(read_at, now()),
    is_reviewed = true,
    reviewed_at = COALESCE(reviewed_at, now()),
    reviewed_by = COALESCE(reviewed_by, 'system'),
    review_notes = CASE
        WHEN COALESCE(review_notes, '') = ''
            THEN 'Automatically archived: historical interview already passed.'
        ELSE review_notes
    END,
    updated_at = now()
WHERE booking_status = 'Historical Skipped'
  AND dismissed_at IS NULL;
