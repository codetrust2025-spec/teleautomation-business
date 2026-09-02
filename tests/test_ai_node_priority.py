"""Which node serves inference, and for how long a person can decide it.

Normal priority is the configured order -- RTX 4060, Jagadeesh, Praveen -- and
the highest node that is not cooling off takes it without anyone asking. A
hand-picked primary outranks that for an hour and then the pool goes back to
choosing for itself, because an override left in place stops being a decision
and becomes the configuration nobody remembers making.

The case worth the most here is the override on a node that then dies. Honouring
it would send every request to a machine known to be down, so it is dropped the
moment the breaker opens -- no one has to remember to cancel it.
"""

from __future__ import annotations

import time

import pytest

from core import ollama_nodes as nodes


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("OLLAMA_PRIMARY_NODE", raising=False)
    monkeypatch.delenv("OLLAMA_ENABLE_VPS_LOCAL", raising=False)
    nodes.reset_breakers()
    yield
    nodes.reset_breakers()


def down(node_id: str) -> None:
    """Open the breaker the way a run of real failures would."""
    for _ in range(nodes._failure_threshold()):
        nodes.record_failure(node_id, "connection refused")


class TestNormalPriority:
    def test_the_order_is_rtx_then_jagadeesh_then_praveen(self):
        assert [n["id"] for n in nodes.configured_nodes()] == [
            "rtx4060", "jagadeesh", "our_machine",
        ]

    def test_the_top_node_is_primary_with_nothing_set(self):
        assert nodes.primary_node_id() == "rtx4060"

    def test_the_next_healthy_node_takes_over_automatically(self):
        down("rtx4060")
        assert nodes.primary_node_id() == "jagadeesh"

    def test_it_walks_the_whole_order(self):
        down("rtx4060")
        down("jagadeesh")
        assert nodes.primary_node_id() == "our_machine"

    def test_it_still_names_a_node_when_everything_is_cooling_off(self):
        for node_id in ("rtx4060", "jagadeesh", "our_machine"):
            down(node_id)
        # Naming the top of the order means recovery is retried there first.
        assert nodes.primary_node_id() == "rtx4060"

    def test_recovery_returns_the_top_node_without_intervention(self):
        down("rtx4060")
        assert nodes.primary_node_id() == "jagadeesh"
        nodes.record_success("rtx4060")
        assert nodes.primary_node_id() == "rtx4060"


class TestTheManualOverride:
    def test_a_chosen_node_outranks_normal_priority(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        assert nodes.primary_node_id() == "our_machine"

    def test_it_lasts_an_hour(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        override = nodes.primary_override()
        assert 3500 < override["expires_in_s"] <= 3600

    def test_it_expires_and_normal_priority_resumes(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        assert nodes.primary_node_id() == "our_machine"

        # An hour and a second later.
        later = time.time() + 3601
        monkeypatch.setattr(nodes.time, "time", lambda: later)
        assert nodes.primary_override() == {}
        assert nodes.primary_node_id() == "rtx4060"

    def test_an_expired_override_is_reported_absent_not_honoured(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("jagadeesh")
        later = time.time() + 3601
        monkeypatch.setattr(nodes.time, "time", lambda: later)
        assert nodes.primary_override() == {}

    def test_it_can_be_handed_back_early(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        nodes.clear_primary_override()
        assert nodes.primary_override() == {}
        assert nodes.primary_node_id() == "rtx4060"


class TestFailoverBeatsTheOverride:
    def test_an_override_on_a_dead_node_fails_over_immediately(self, monkeypatch):
        """The case this exists for. Keeping the override would send every
        request to a machine known to be down."""
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        assert nodes.primary_node_id() == "our_machine"

        down("our_machine")
        assert nodes.primary_node_id() == "rtx4060"

    def test_the_override_is_not_destroyed_by_the_failover(self, monkeypatch):
        """It is out-ranked while the node is down, not cancelled: if the node
        comes back inside the hour the person's choice still stands."""
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        down("our_machine")
        assert nodes.primary_node_id() == "rtx4060"

        nodes.record_success("our_machine")
        assert nodes.primary_node_id() == "our_machine"

    def test_failover_walks_past_a_second_dead_node(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("our_machine")
        down("our_machine")
        down("rtx4060")
        assert nodes.primary_node_id() == "jagadeesh"


class TestOnlyOnePrimary:
    def test_exactly_one_node_is_primary_in_every_arrangement(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        arrangements = (
            (),
            ("rtx4060",),
            ("rtx4060", "jagadeesh"),
            ("rtx4060", "jagadeesh", "our_machine"),
            ("jagadeesh",),
        )
        for downed in arrangements:
            nodes.reset_breakers()
            for node_id in downed:
                down(node_id)
            chosen = nodes.primary_node_id()
            flags = [n["id"] == chosen for n in nodes.configured_nodes()]
            assert sum(flags) == 1, f"with {downed} down, primary flags were {flags}"

    def test_setting_a_primary_replaces_the_previous_one(self, monkeypatch):
        monkeypatch.setattr(nodes, "missing_models", lambda *a, **k: [])
        nodes.set_primary_node("jagadeesh")
        nodes.set_primary_node("our_machine")
        assert nodes.primary_node_id() == "our_machine"
        assert nodes.primary_override()["node_id"] == "our_machine"


class TestTheStateSurvivesADeploy:
    def test_it_is_written_to_the_data_volume(self, monkeypatch):
        """`/app/data` is part of the image, so a release replaced it and could
        discard a one-hour override ten minutes in."""
        monkeypatch.delenv("OLLAMA_NODE_STATE_FILE", raising=False)
        assert "/app/data/" not in str(nodes._state_path()).replace("\\", "/")
        source = (nodes.__file__ or "")
        assert source
        from pathlib import Path

        assert "from core.config import DATA_DIR" in Path(source).read_text(encoding="utf-8")
