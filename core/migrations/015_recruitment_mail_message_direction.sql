ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS message_direction text;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS gmail_label_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS to_metadata jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS mailbox_messages_direction_idx
  ON mailbox_messages(mailbox_id, message_direction, sent_at DESC);
