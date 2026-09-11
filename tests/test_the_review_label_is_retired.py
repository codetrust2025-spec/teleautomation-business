"""The review label is retired at the source, not just in the data.

Migration 033 clears `review_status='PENDING'` and `requires_manual_review`
from 155 historical events. A data fix alone would be worthless if the code
that wrote the label were still writing it, so these tests pin the source.

`create_event` used to file anything that was not auto-validated as awaiting a
person:

    review_state = 'AUTO_VALIDATED' if validation_status=='AUTO_VALIDATED'
                   else 'PENDING'

which caught medium confidence, reconciled disagreements and interview
activity with no bookable time -- roughly 40% of events on the days measured.
0b199cf replaced it with a flat 'AUTOMATED'. These tests fail if it comes back.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from core import recruitment_mail_store as store

MIGRATION = Path(__file__).resolve().parent.parent / "core" / "migrations" / "033_retire_the_review_label.sql"


class TestNothingWritesThePendingLabelAnyMore:
    @pytest.mark.parametrize("name", ["create_event", "create_or_reprocess_event"])
    def test_the_event_paths_write_automated(self, name):
        source = inspect.getsource(getattr(store, name))
        assert "review_state='AUTOMATED'" in source

    @pytest.mark.parametrize("name", ["create_event", "create_or_reprocess_event"])
    def test_they_do_not_branch_on_validation_status(self, name):
        """The old expression, in any spacing."""
        source = inspect.getsource(getattr(store, name))
        collapsed = re.sub(r"\s+", "", source)
        assert "else'PENDING'" not in collapsed

    def test_no_insert_leaves_the_column_to_its_default(self):
        """The column still defaults to 'PENDING', so an INSERT that omits it
        would quietly refill the population this migration clears."""
        source = Path(store.__file__).read_text(encoding="utf-8")
        for match in re.finditer(r"INSERT INTO ai_recruitment_events\((.*?)\)", source, re.S):
            assert "review_status" in match.group(1)
            assert "requires_manual_review" in match.group(1)


class TestTheMigrationOnlyTouchesTheLabel:
    def test_it_updates_exactly_the_two_review_columns(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        statements = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
        body = "\n".join(statements)
        assigned = set(re.findall(r"SET\s+(\w+)\s*=|^\s{3,}(\w+)\s*=", body, re.M))
        columns = {a or b for a, b in assigned}
        assert columns == {"review_status", "requires_manual_review", "updated_at"}

    def test_it_never_touches_booking_or_lifecycle_columns(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
        for column in ("primary_status", "classification", "interview_date", "interview_time",
                       "structured_result", "validation_status", "automation_state",
                       "candidate_status", "confidence"):
            assert column not in body

    def test_it_touches_no_other_table(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
        assert set(re.findall(r"UPDATE\s+(\w+)", body)) == {"ai_recruitment_events"}
        assert "DELETE" not in body.upper()
        assert "DROP" not in body.upper()

    def test_it_preserves_real_operator_outcomes(self):
        """FALSE_POSITIVE and IGNORED say what a person decided."""
        sql = MIGRATION.read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
        assert "WHERE review_status = 'PENDING'" in body
        assert "FALSE_POSITIVE" not in body

    def test_it_is_idempotent(self):
        """Re-running matches nothing, which is what makes a checksum-tracked
        migration safe to reason about after the fact."""
        sql = MIGRATION.read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
        assert body.count("WHERE review_status = 'PENDING'") == 1
        assert body.count("WHERE requires_manual_review = TRUE") == 1


class TestTheMetricDoesNotLoseGenuineWork:
    def test_automation_pending_still_counts_retry_state_independently(self):
        """The clause this migration empties is the third of four. Rows that
        are genuinely pending automation match on automation_state or
        primary_status, which the migration does not touch -- measured on
        production, 88 rows matched via review_status and 0 of them relied
        on it."""
        source = inspect.getsource(store.summarize_selection_tracking_events)
        start = source.index("filters['automation_pending']")
        clause = source[start:source.index("metrics=", start)]
        assert "automation_state" in clause
        assert "AI_RETRY_PENDING" in clause
        # The review_status clause is not the only way in.
        assert clause.count("AI_RETRY_PENDING") >= 2
