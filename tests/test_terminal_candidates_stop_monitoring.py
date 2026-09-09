"""A closed candidate's Gmail is history, not work.

Ram Charan M S was `completed`, yet his mailbox still counted as Reconnect
Required and would have been synced indefinitely. Nothing in the mailbox schema
knew about candidate stage: `schedule_due`, the watch renewal and the health
rows all keyed on `monitoring_enabled` and `connection_status` alone.

The linkage and history are kept. What changes is that stage decides whether a
mailbox is *active work*, derived on every read, so returning a candidate to
in_progress restores monitoring with no migration and no flag to unset.

The fail-safe direction matters. A mailbox's candidate_id is often an alias: 11
of 21 production mailboxes did not resolve through the identity-link column and
needed `canonical_candidate_identity_id`, and every one of them was in_progress.
An unresolved mailbox is therefore treated as ACTIVE. Guessing the other way
would silently stop monitoring a live candidate's mail.
"""

from __future__ import annotations

import inspect

import pytest

from core import recruitment_mail_store as store


class TestWhichStagesEndMonitoring:
    def test_the_three_terminal_stages(self):
        assert store.TERMINAL_CANDIDATE_STAGES == {"completed", "fail", "dropped"}

    def test_in_progress_is_not_terminal(self):
        assert "in_progress" not in store.TERMINAL_CANDIDATE_STAGES

    def test_it_matches_the_candidate_vocabulary(self):
        from features.candidate_store import VALID_STAGES

        assert store.TERMINAL_CANDIDATE_STAGES < VALID_STAGES
        assert VALID_STAGES - store.TERMINAL_CANDIDATE_STAGES == {"in_progress"}


class TestResolutionFailsSafe:
    """An unresolved mailbox keeps monitoring. This is the whole safety story."""

    def test_it_resolves_through_the_canonical_identity(self):
        source = inspect.getsource(store.terminal_mailbox_ids)
        assert "canonical_candidate_identity_id" in source

    def test_only_a_known_terminal_stage_excludes(self):
        source = inspect.getsource(store.terminal_mailbox_ids)
        assert "if stage in TERMINAL_CANDIDATE_STAGES" in source
        # Never the inverse: excluding "not active" would sweep up every
        # mailbox whose candidate could not be resolved.
        assert "not in TERMINAL_CANDIDATE_STAGES" not in source

    def test_a_resolution_error_does_not_exclude(self):
        source = inspect.getsource(store.terminal_mailbox_ids)
        assert "except Exception" in source

    def test_it_asks_for_every_stage_not_just_active_ones(self):
        """list_candidates(stage="all") -- filtering to in_progress here would
        make every terminal candidate simply unresolved, and so active."""
        assert 'stage="all"' in inspect.getsource(store.terminal_mailbox_ids)


class TestTheHealthRowsCarryIt:
    def test_every_row_is_annotated(self):
        source = inspect.getsource(store._mailbox_health_rows)
        assert 'row["monitoring_excluded"] = str(row.get("id")) in terminal' in source

    def test_rows_are_annotated_not_dropped(self):
        """The mailbox stays listed so its history and linkage remain visible
        and reconnectable."""
        source = inspect.getsource(store._mailbox_health_rows)
        assert "monitoring_excluded" in source
        for removal in ("del ", "rows = [row for row in rows if"):
            assert removal not in source


class TestTheSyncSchedulerSkipsThem:
    @staticmethod
    def _worker_source(name):
        from workers import recruitment_mail_worker as worker

        return inspect.getsource(getattr(worker.RecruitmentMailWorker, name))

    def test_scheduling_excludes_terminal_mailboxes(self):
        source = self._worker_source("schedule_due")
        assert "_terminal_mailbox_ids(cur)" in source
        assert "NOT (m.id = ANY(%s))" in source

    def test_watch_renewal_excludes_them_too(self):
        """Otherwise Gmail keeps pushing for a mailbox nobody is working."""
        source = self._worker_source("renew_due_watches")
        assert "_terminal_mailbox_ids(cur)" in source
        assert "NOT (id = ANY(%s))" in source

    def test_active_mailboxes_are_otherwise_unchanged(self):
        source = self._worker_source("schedule_due")
        assert "m.monitoring_enabled=true" in source
        assert "m.connection_status='CONNECTED'" in source


class TestTheOverviewGainsNoRoundTrip:
    def test_it_reuses_the_rows_already_fetched(self):
        """The mailbox overview is polled while syncs run and once starved the
        API worker, so this must not add a query to it."""
        source = inspect.getsource(store._mailbox_health_rows)
        assert "terminal_mailbox_ids(rows)" in source
        assert source.count("cur.execute") == 1

    def test_the_resolver_itself_never_queries(self):
        source = inspect.getsource(store.terminal_mailbox_ids)
        assert "cur.execute" not in source
        assert "get_connection" not in source


class TestReactivationNeedsNoCleanup:
    def test_the_exclusion_is_derived_not_stored(self):
        """No column, no flag, no migration: stage is read on every pass, so a
        candidate returning to in_progress is monitored again immediately."""
        source = inspect.getsource(store.terminal_mailbox_ids)
        assert "UPDATE" not in source.upper().replace("UPDATED_AT", "")
        assert "monitoring_enabled" not in source

    def test_nothing_deletes_a_mailbox_or_its_credentials(self):
        for name in ("terminal_mailbox_ids", "_mailbox_health_rows"):
            source = inspect.getsource(getattr(store, name))
            assert "DELETE" not in source.upper()
            assert "credential_ciphertext=NULL" not in source
