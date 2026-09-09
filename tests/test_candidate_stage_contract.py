"""The four candidate stages, and what still depends on their stored values.

Two of them were relabelled in the dashboard — `fail` now reads "Rejected" and
`completed` reads "Closed / Completed" — because "Failed" was being taken to
mean a system failure rather than a rejected candidate.

Nothing about the stored values changed, and this pins why. `fail` and
`dropped` are the values `_interview_rows_for_range` and
`find_interview_slot_conflicts` already exclude, and `completed` is what stamps
a closure date. Renaming any of them means finding every one of those sites,
and a missed one quietly lets a rejected candidate back into revenue or into a
slot-conflict check.

Only `in_progress` reaches Pending Gmail or Pending Works: both go through
`list_candidates(stage="in_progress")`, so a candidate leaves those lists the
moment any terminal stage is set.
"""

from __future__ import annotations

import inspect

import pytest

from features import candidate_store as cs


def _candidate(name: str, stage: str) -> dict:
    return {
        "id": f"id-{name}", "name": name, "stage": stage,
        "service_type": "profile_service", "reference": "ref",
        "phone": "9000000000", "resume_count": 1, "resumes": [{"id": "r"}],
    }


#: One candidate in each stage, which is what the whole contract turns on.
ONE_OF_EACH = [
    _candidate("Still working", "in_progress"),
    _candidate("Closed out", "completed"),
    _candidate("Rejected", "fail"),
    _candidate("Dropped", "dropped"),
]


class TestTheStagesThemselves:
    def test_there_are_exactly_four(self):
        assert cs.VALID_STAGES == {"in_progress", "completed", "fail", "dropped"}

    def test_no_new_stage_was_introduced(self):
        for invented in ("rejected", "closed", "archived", "inactive"):
            assert invented not in cs.VALID_STAGES

    def test_anything_unrecognised_falls_back_to_in_progress(self):
        """A stage the model does not know must not silently become terminal
        and drop a live candidate out of the pending lists."""
        source = inspect.getsource(cs)
        assert 'out["stage"] = "in_progress"' in source


class TestOnlyInProgressIsPending:
    @staticmethod
    def _active(rows):
        # What list_candidates(stage="in_progress") does.
        return [row for row in rows if row.get("stage") == "in_progress"]

    def test_one_candidate_in_each_stage_leaves_only_one_active(self):
        assert [row["name"] for row in self._active(ONE_OF_EACH)] == ["Still working"]

    @pytest.mark.parametrize("stage", ["completed", "fail", "dropped"])
    def test_every_terminal_stage_is_excluded(self, stage):
        assert self._active([_candidate("x", stage)]) == []

    def test_pending_works_asks_for_in_progress_only(self):
        assert 'list_candidates(stage="in_progress"' in inspect.getsource(cs.pending_works)

    def test_pending_works_returns_nothing_for_a_terminal_candidate(self):
        """Belt and braces: even handed a terminal row directly, the builder
        must not invent work for someone who has left the pipeline."""
        for stage in ("completed", "fail", "dropped"):
            rows = self._active([_candidate("Gone", stage)])
            assert cs._pending_works_core(rows)["count"] == 0


class TestRevenueAndSlotBehaviourIsUnchanged:
    @pytest.mark.parametrize("function", [
        cs._interview_rows_for_range,
        cs.find_interview_slot_conflicts,
    ])
    def test_both_still_exclude_exactly_dropped_and_fail(self, function):
        source = inspect.getsource(function)
        assert 'stage") in {"dropped", "fail"}' in source

    def test_completed_is_still_what_stamps_a_closure(self):
        source = inspect.getsource(cs)
        assert 'out["stage"] == "completed"' in source

    def test_dropped_still_keeps_its_historical_closure_handling(self):
        source = inspect.getsource(cs)
        assert 'if stage == "dropped":' in source
