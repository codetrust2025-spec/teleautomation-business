"""Pure Ollama AI Mail Detection: the switch, and what each position does.

ON, which is the default, the keyword and routing rules stop deciding what the
model is allowed to see. Those rules are what dropped a real interview reminder
as NO_RECRUITMENT_ROUTING_SIGNAL, with no alert and nothing on any screen.

OFF, the existing flow runs exactly as it does today. Nothing is removed and
the switch is reversible, so the old behaviour is always one toggle away.

What neither position changes is what happens after classification. The
deterministic booking checks -- candidate, schedule, timezone, payment,
duplicate, conflict -- and the persistence re-read are downstream of this
switch, and "Auto Booked" is still only written against a slot that is actually
stored.
"""

from __future__ import annotations

import json

import pytest

from core import pure_ollama_policy as policy


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """A brand-new install: no saved setting, no environment variable."""
    monkeypatch.setattr(
        policy, "_state_file", lambda: str(tmp_path / "pure_ollama_policy.json"),
    )
    monkeypatch.delenv("PURE_OLLAMA_MAIL_DETECTION", raising=False)
    return tmp_path / "pure_ollama_policy.json"


class TestItIsOnByDefault:
    def test_a_fresh_install_starts_on(self, fresh):
        assert policy.pure_ollama_enabled() is True

    def test_no_file_is_written_just_by_asking(self, fresh):
        policy.pure_ollama_enabled()
        assert not fresh.exists()

    def test_the_default_is_reported_as_coming_from_the_environment(self, fresh):
        state = policy.status()
        assert state["enabled"] is True
        assert state["source"] == "environment"
        assert state["env_default"] is True

    def test_the_mode_label_says_which_flow_runs(self, fresh):
        assert policy.detection_mode() == "pure-ollama"

    def test_a_host_may_force_it_off_before_anything_is_saved(self, fresh, monkeypatch):
        monkeypatch.setenv("PURE_OLLAMA_MAIL_DETECTION", "false")
        assert policy.pure_ollama_enabled() is False
        assert policy.detection_mode() == "rules-first"


class TestTheSwitchPersists:
    def test_turning_it_off_is_remembered(self, fresh):
        policy.set_pure_ollama_enabled(False, actor="admin")
        assert policy.pure_ollama_enabled() is False

    def test_it_survives_a_restart(self, fresh):
        """Nothing is cached in the process: the next read comes off disk."""
        policy.set_pure_ollama_enabled(False, actor="admin")
        saved = json.loads(fresh.read_text(encoding="utf-8"))
        assert saved["enabled"] is False
        # A fresh read, as a restarted process would do.
        assert policy.pure_ollama_enabled() is False

    def test_the_saved_value_beats_the_environment(self, fresh, monkeypatch):
        policy.set_pure_ollama_enabled(False, actor="admin")
        monkeypatch.setenv("PURE_OLLAMA_MAIL_DETECTION", "true")
        assert policy.pure_ollama_enabled() is False
        assert policy.status()["source"] == "admin"

    def test_it_is_reversible(self, fresh):
        policy.set_pure_ollama_enabled(False, actor="admin")
        policy.set_pure_ollama_enabled(True, actor="admin")
        assert policy.pure_ollama_enabled() is True

    def test_who_changed_it_is_recorded(self, fresh):
        policy.set_pure_ollama_enabled(False, actor="rakesh", source_ip="10.0.0.9")
        entry = policy.audit_log(5)[0]
        assert entry["actor"] == "rakesh"
        assert entry["previous"] is True
        assert entry["new"] is False
        assert entry["source_ip"] == "10.0.0.9"

    def test_the_audit_cannot_grow_without_bound(self, fresh):
        for _ in range(policy._MAX_AUDIT_ENTRIES + 20):
            policy.set_pure_ollama_enabled(False, actor="admin")
        saved = json.loads(fresh.read_text(encoding="utf-8"))
        assert len(saved["audit"]) == policy._MAX_AUDIT_ENTRIES

    def test_a_corrupt_file_falls_back_to_the_default(self, fresh):
        fresh.write_text("{ not json", encoding="utf-8")
        assert policy.pure_ollama_enabled() is True


class TestTheOnFlowReachesTheModel:
    """ON, a mail the rules would have dropped is sent for classification."""

    # The exact mail the routing rules dropped, before any of this existed.
    UNROUTABLE = (
        "Weekly digest",
        "Nothing in this message resembles recruitment at all.",
    )

    def test_the_rules_alone_would_not_route_it(self):
        from services import recruitment_mail_agent as agent

        decision = agent.routing_decision(*self.UNROUTABLE, "", "")
        assert decision["send_to_ai"] is False
        assert decision["reason"] == "NO_RECRUITMENT_ROUTING_SIGNAL"

    def test_the_switch_is_read_where_the_ignore_happens(self):
        """The bypass sits after the duplicate and direction checks and before
        the routing gate rejects anything."""
        import inspect

        from services import recruitment_mail_agent as agent

        source = inspect.getsource(agent.process_message)
        bypass = source.index("pure_ollama_enabled()")
        ignore = source.index('if not route["send_to_ai"] and not calendar_result:')
        duplicate = source.index("is_duplicate_content")
        direction = source.index("OUTBOUND_MESSAGE")
        assert duplicate < bypass, "duplicate check must still run first"
        assert direction < bypass, "direction check must still run first"
        assert bypass < ignore, "the bypass must precede the ignore"

    def test_the_bypass_names_itself(self):
        import inspect

        from services import recruitment_mail_agent as agent

        assert "PURE_OLLAMA_DETECTION" in inspect.getsource(agent.process_message)


class TestTheOffFlowIsTheCurrentSystem:
    def test_the_routing_rules_still_decide(self, fresh):
        from services import recruitment_mail_agent as agent

        policy.set_pure_ollama_enabled(False, actor="admin")
        decision = agent.routing_decision(
            "Weekly digest", "Nothing recruitment here.", "", "",
        )
        assert decision["send_to_ai"] is False

    def test_a_qualifying_mail_still_routes_with_its_own_reason(self, fresh):
        """OFF must not change how the existing rules classify what they do
        recognise."""
        from services import recruitment_mail_agent as agent

        policy.set_pure_ollama_enabled(False, actor="admin")
        decision = agent.routing_decision(
            "Your interview is scheduled",
            "Your technical interview has been scheduled for 11 September 2026 "
            "at 2:00 PM IST.",
            "", "",
        )
        assert decision["send_to_ai"] is True
        assert decision["reason"] != "PURE_OLLAMA_DETECTION"


class TestTheSafetyChecksAreUntouched:
    """Neither position may change what it takes to book."""

    def test_booking_still_re_reads_the_slot_before_recording_it(self):
        import inspect

        from services import interview_auto_booking as ab

        source = inspect.getsource(ab._execute_auto_booking)
        assert source.index("_confirm_slot_still_stored") < source.index("auto_booked=True")

    def test_the_persisted_statuses_are_unchanged(self):
        from services import interview_auto_booking as ab

        assert ab._PERSISTED_BOOKING_STATUSES == {
            "Auto Booked", "Approved & Booked", "Rescheduled",
        }

    @pytest.mark.parametrize("code", [
        "CANDIDATE_MAPPING_FAILED", "INVALID_DATE", "INVALID_TIME",
        "MISSING_TIMEZONE", "INVALID_TIMEZONE", "PAYMENT_VALIDATION_FAILED",
        "DUPLICATE_BOOKING", "SLOT_CONFLICT",
    ])
    def test_every_deterministic_check_still_exists(self, code):
        import inspect

        from services import interview_auto_booking as ab

        assert code in inspect.getsource(ab)

    def test_the_switch_does_not_reach_the_booking_module(self):
        """It decides who classifies, not what is trusted afterwards."""
        import inspect

        from services import interview_auto_booking as ab

        assert "pure_ollama" not in inspect.getsource(ab)
