"""Today's mail must not wait behind a month of backlog.

On 2026-09-09 Mail Alerts stopped at 11:01 and showed nothing new for six
hours. Nothing was broken: Gmail ingestion stored all 132 mails that arrived
after 11:00, the worker was alive, classification and the notification API were
both healthy, and the API returned exactly the 82 alerts the screen showed.

The queue was the problem. `claim_ai_messages` ordered strictly `sent_at ASC`,
which is FIFO across the whole table, and 3,403 messages were queued with 2,871
of them older than two days. Every new mail joined the back of a queue whose
head was 2026-08-17, so at ~174/hour it would have been about twenty hours
before live mail was reached at all.

Recent mail is now claimed first, oldest-first within each tier, so the backlog
still drains whenever nothing live is waiting.
"""

from __future__ import annotations

import inspect

import pytest

from core import recruitment_mail_store as store


def _claim_sql() -> str:
    return inspect.getsource(store.claim_ai_messages)


class TestTheClaimOrder:
    def test_recent_mail_is_claimed_before_the_backlog(self):
        sql = _claim_sql()
        assert "ORDER BY (COALESCE(sent_at,created_at) >= now()-(%s||' hours')::interval) DESC" in sql

    def test_it_is_no_longer_plain_fifo_over_everything(self):
        """The exact ordering that stalled Mail Alerts."""
        assert "ORDER BY sent_at ASC,id" not in _claim_sql()

    def test_each_tier_is_still_oldest_first(self):
        """Within live mail and within the backlog, the oldest still goes
        first: this prioritises, it does not turn the queue into a stack."""
        sql = _claim_sql()
        tier = sql.index("::interval) DESC")
        assert "sent_at ASC,id" in sql[tier:tier + 120]

    def test_a_null_sent_at_cannot_jump_the_queue(self):
        """Without COALESCE the tier is NULL for those rows, and NULL sorts
        first under DESC -- every undated message would pre-empt live mail."""
        assert "COALESCE(sent_at,created_at)" in _claim_sql()

    def test_the_backlog_is_still_claimable(self):
        """One query, both tiers: nothing filters old mail out, so it is taken
        whenever no live mail is waiting."""
        sql = _claim_sql()
        assert "processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')" in sql
        for excluded in ("AND sent_at >", "AND created_at >"):
            assert excluded not in sql

    def test_the_attempt_cap_and_backoff_are_untouched(self):
        sql = _claim_sql()
        assert "COALESCE(ai_retry_after,now())<=now()" in sql
        assert "COALESCE(ai_retry_count,0)<%s" in sql
        assert "FOR UPDATE SKIP LOCKED" in sql


class TestTheLiveWindow:
    def test_defaults_to_half_a_day(self, monkeypatch):
        """Chosen from the queue: at ~206 messages/hour a 48-hour window left
        520 ahead of today's mail, 12 hours leaves 188."""
        monkeypatch.delenv("AI_MAIL_LIVE_WINDOW_HOURS", raising=False)
        assert store._live_mail_window_hours() == 12

    def test_a_host_can_change_it(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_LIVE_WINDOW_HOURS", "6")
        assert store._live_mail_window_hours() == 6

    @pytest.mark.parametrize("raw,expected", [
        ("0", 1), ("-5", 1),        # never a window that excludes everything
        ("100000", 720),            # never so wide that the tier means nothing
        ("nonsense", 12), ("", 12),  # unreadable falls back to the default
    ])
    def test_it_stays_within_sane_bounds(self, monkeypatch, raw, expected):
        monkeypatch.setenv("AI_MAIL_LIVE_WINDOW_HOURS", raw)
        assert store._live_mail_window_hours() == expected

    def test_the_window_is_passed_to_the_query(self):
        assert "_live_mail_window_hours()" in _claim_sql()


class TestNothingElseAboutTheQueueChanged:
    def test_terminal_parking_still_happens_first(self):
        """Rows past the attempt cap are still parked before anything is
        claimed, so they cannot be handed out again."""
        sql = _claim_sql()
        assert "AI_FAILED_TERMINAL" in sql
        assert sql.index("AI_FAILED_TERMINAL") < sql.index("ORDER BY")

    def test_expired_leases_are_still_returned_to_the_queue(self):
        sql = _claim_sql()
        assert "LEASE_EXPIRED" in sql
        assert "processing_status='AI_QUEUED'" in sql

    def test_the_batch_is_still_capped(self):
        assert "max(1,min(limit,20))" in _claim_sql()
