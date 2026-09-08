"""Every stored attendance status gets counted as itself.

`_interview_attendance_counts` named four statuses in an if/elif chain and
derived Pending by subtracting those four from the row count. `re_service` was
in INTERVIEW_ATTENDANCE_STATUSES but not in the chain, so every Re-Service row
fell through the subtraction and was reported as Pending -- on all five
surfaces that call this, including the Daily Ops tabs.

The counts are the contract the dashboard filters against, so a status that
cannot be counted cannot honestly be filtered either.
"""

from __future__ import annotations

import pytest

from features import candidate_store as cs


def rows(*statuses: str) -> list[dict]:
    return [{"interview_attendance_status": status} for status in statuses]


class TestTheStatusSetIsTheSourceOfTruth:
    def test_the_six_statuses_are_what_the_dashboard_shows(self):
        assert cs.INTERVIEW_ATTENDANCE_STATUSES == frozenset({
            "attended", "not_attended", "cancelled", "rescheduled", "re_service",
        })

    def test_pending_is_the_absence_of_one_not_a_member(self):
        """Pending is derived, never stored -- it must not be in the set."""
        assert "pending" not in cs.INTERVIEW_ATTENDANCE_STATUSES
        assert cs.normalise_interview_attendance_status("pending") == ""
        assert cs.normalise_interview_attendance_status("") == ""

    def test_every_stored_status_has_its_own_counter(self):
        counts = cs._interview_attendance_counts([])
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert f"{status}_count" in counts, f"{status} has no counter"
        assert "pending_count" in counts


class TestCountingIsExact:
    def test_each_status_counts_once(self):
        counts = cs._interview_attendance_counts(rows(
            "attended", "attended", "not_attended", "cancelled",
            "rescheduled", "re_service", "",
        ))
        assert counts == {
            "attended_count": 2,
            "not_attended_count": 1,
            "cancelled_count": 1,
            "rescheduled_count": 1,
            "re_service_count": 1,
            "pending_count": 1,
        }

    def test_re_service_is_not_reported_as_pending(self):
        """The defect, stated on its own."""
        counts = cs._interview_attendance_counts(rows("re_service", "re_service"))
        assert counts["re_service_count"] == 2
        assert counts["pending_count"] == 0

    def test_cancelled_is_counted_as_cancelled(self):
        counts = cs._interview_attendance_counts(rows("cancelled", "cancelled", ""))
        assert counts["cancelled_count"] == 2
        assert counts["pending_count"] == 1

    def test_only_rows_without_a_status_are_pending(self):
        counts = cs._interview_attendance_counts(rows("", "", ""))
        assert counts["pending_count"] == 3
        assert sum(v for k, v in counts.items() if k != "pending_count") == 0

    def test_the_counters_add_up_to_the_row_count(self):
        every = rows("attended", "not_attended", "cancelled", "rescheduled",
                     "re_service", "", "attended")
        counts = cs._interview_attendance_counts(every)
        assert sum(counts.values()) == len(every)

    @pytest.mark.parametrize("stored,counted", [
        ("canceled", "cancelled_count"),
        ("reschedule", "rescheduled_count"),
        ("CANCELLED", "cancelled_count"),
    ])
    def test_legacy_spellings_land_on_the_right_counter(self, stored, counted):
        counts = cs._interview_attendance_counts(rows(stored))
        assert counts[counted] == 1
        assert counts["pending_count"] == 0

    def test_an_unknown_status_is_pending_rather_than_lost(self):
        counts = cs._interview_attendance_counts(rows("nonsense"))
        assert counts["pending_count"] == 1
        assert sum(counts.values()) == 1


class TestCancelledIsNeverMappedElsewhere:
    @pytest.mark.parametrize("spelling", ["cancelled", "canceled", "Cancelled", " CANCELED "])
    def test_it_normalises_to_cancelled_and_nothing_else(self, spelling):
        assert cs.normalise_interview_attendance_status(spelling) == "cancelled"

    def test_a_cancelled_row_reads_back_as_cancelled(self):
        assert cs.row_interview_attendance_status(
            {"interview_attendance_status": "cancelled"}
        ) == "cancelled"

    def test_cancelled_survives_a_legacy_attended_flag(self):
        """`interview_attended` is only a fallback for rows with no status."""
        assert cs.row_interview_attendance_status(
            {"interview_attendance_status": "cancelled", "interview_attended": True}
        ) == "cancelled"


class TestTheBreakdownBucketsAgree:
    """by_attendee / by_referrer / by_candidate / by_technology in the global
    summary carried the same four-status chain, with everything else swept into
    "pending" -- so Re-Service was pending there too."""

    def test_a_bucket_has_a_slot_for_every_status(self):
        counts = cs._interview_attendance_counts([])
        # Every counter the summary reports has a matching bucket key.
        bucket_keys = set(cs.INTERVIEW_ATTENDANCE_STATUSES) | {"scheduled", "pending"}
        for key in counts:
            assert key.removesuffix("_count") in bucket_keys

    def test_the_global_summary_breaks_re_service_out(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
        monkeypatch.setattr(cs, "_load_cache", None)
        monkeypatch.setattr(cs, "_load_cache_at", 0.0)
        monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

        # The dashboard reads these from `interviews`, which is where the
        # counts are spread — it is the object the KPI tabs index into.
        summary = cs.interview_global_summary("2026-09-01", "2026-09-30")
        interviews = summary["interviews"]
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert f"{status}_count" in interviews
        assert "pending_count" in interviews
        assert "count" in interviews


class TestTheRosterPayloadCarriesThem:
    def test_daily_roster_exposes_a_counter_per_status(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
        monkeypatch.setattr(cs, "_load_cache", None)
        monkeypatch.setattr(cs, "_load_cache_at", 0.0)
        monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

        payload = cs.daily_interview_roster("2026-09-07")
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert f"{status}_count" in payload
        assert "pending_count" in payload
        assert "count" in payload
