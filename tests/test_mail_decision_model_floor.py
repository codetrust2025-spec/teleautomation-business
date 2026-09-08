"""No weak model may decide a candidate's mail.

Pure Ollama detection sends every inbound mail to the model, so the model *is*
the detector. Which one answers is therefore a correctness setting, not a
performance one, and the rule is that accuracy wins: mail classification,
validation and reasoning run on the best installed text model, and vision runs
only where an image actually has to be read.

The concrete reason this is enforced rather than documented: production shipped
with ``AI_RECRUITMENT_MODEL=gemma2:2b`` sitting in its env file. It was inert
only because ``OLLAMA_PRIMARY_MODEL`` happened to be set and outranks it in
``model_for``. One unset variable and a 2B model would have been classifying
interview mail, silently and with no error anywhere.
"""

from __future__ import annotations

import logging

import pytest

from core import ai_model_routing as routing
from core.ai_model_routing import (
    MIN_TEXT_DECISION_PARAMS_B,
    SAFE_TEXT_DECISION_MODEL,
    TEXT_DECISION_ROUTES,
    is_weak_text_model,
    model_for,
    model_parameter_billions,
)

#: Every variable that can name a model, cleared so each test states its own.
_MODEL_VARS = (
    "OLLAMA_PRIMARY_MODEL", "AI_RECRUITMENT_VALIDATOR_MODEL", "OLLAMA_VISION_MODEL",
    "OLLAMA_PAYMENT_VISION_MODEL", "OLLAMA_REASONING_MODEL", "OLLAMA_FALLBACK_MODEL",
    "AI_RECRUITMENT_FALLBACK_MODEL", "OLLAMA_MAIL_MODEL", "AI_RECRUITMENT_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _MODEL_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestReadingTheSizeOffATag:
    @pytest.mark.parametrize("tag,expected", [
        ("qwen2.5:7b", 7.0),
        ("qwen3:8b", 8.0),
        ("qwen3:14b", 14.0),
        ("qwen3.6:35b", 35.0),
        ("qwen3-vl:8b-instruct", 8.0),
        ("gemma2:2b", 2.0),
        ("qwen2.5:0.5b", 0.5),
        ("qwen2.5:14b-instruct-q4_K_M", 14.0),
    ])
    def test_a_tag_that_states_its_size(self, tag, expected):
        assert model_parameter_billions(tag) == expected

    @pytest.mark.parametrize("tag", ["qwen3.6:latest", "qwen2.5", "", "   ", "sha256:abcdef"])
    def test_a_tag_that_does_not_state_its_size(self, tag):
        assert model_parameter_billions(tag) is None

    def test_an_unjudgeable_tag_is_not_called_weak(self):
        """Refusing what cannot be measured would break naming schemes that are
        perfectly fine."""
        assert is_weak_text_model("qwen3.6:latest") is False

    @pytest.mark.parametrize("tag,weak", [
        ("gemma2:2b", True), ("qwen2.5:0.5b", True), ("qwen2.5:3b", True),
        ("qwen2.5:7b", False), ("qwen3:8b", False), ("qwen3:14b", False),
    ])
    def test_the_floor_is_seven_billion(self, tag, weak):
        assert is_weak_text_model(tag) is weak
        assert MIN_TEXT_DECISION_PARAMS_B == 7.0


class TestTheStaleProductionVariableCannotDowngradeAnything:
    def test_the_legacy_variable_alone_cannot_install_a_2b_classifier(self, clean_env):
        """The exact production configuration, minus the one variable that was
        masking it."""
        clean_env.setenv("AI_RECRUITMENT_MODEL", "gemma2:2b")
        assert model_for("recruitment_email_primary") == SAFE_TEXT_DECISION_MODEL

    def test_the_other_legacy_variable_cannot_either(self, clean_env):
        clean_env.setenv("OLLAMA_MAIL_MODEL", "gemma2:2b")
        assert model_for("recruitment_email_primary") == SAFE_TEXT_DECISION_MODEL

    def test_naming_it_explicitly_is_refused_too(self, clean_env):
        """There is no override. Speed is never a reason to answer mail with a
        small model, so an explicit setting is refused exactly like a stale one."""
        clean_env.setenv("OLLAMA_PRIMARY_MODEL", "gemma2:2b")
        assert model_for("recruitment_email_primary") == SAFE_TEXT_DECISION_MODEL

    @pytest.mark.parametrize("route,variable", [
        ("recruitment_email_primary", "OLLAMA_PRIMARY_MODEL"),
        ("recruitment_email_validator", "AI_RECRUITMENT_VALIDATOR_MODEL"),
        ("reasoning_text", "OLLAMA_REASONING_MODEL"),
    ])
    def test_every_decision_route_holds_the_floor(self, clean_env, route, variable):
        clean_env.setenv(variable, "gemma2:2b")
        assert model_for(route) == SAFE_TEXT_DECISION_MODEL

    def test_the_refusal_is_logged_where_someone_will_see_it(self, clean_env, caplog):
        clean_env.setenv("OLLAMA_PRIMARY_MODEL", "gemma2:2b")
        with caplog.at_level(logging.ERROR, logger=routing.__name__):
            model_for("recruitment_email_primary")
        assert "Refusing weak model" in caplog.text
        assert "gemma2:2b" in caplog.text


class TestUpgradesStillWork:
    def test_a_larger_model_passes_untouched(self, clean_env):
        clean_env.setenv("OLLAMA_PRIMARY_MODEL", "qwen3:14b")
        assert model_for("recruitment_email_primary") == "qwen3:14b"

    def test_a_tag_without_a_size_passes_untouched(self, clean_env):
        clean_env.setenv("OLLAMA_PRIMARY_MODEL", "qwen3.6:latest")
        assert model_for("recruitment_email_primary") == "qwen3.6:latest"

    def test_the_floor_model_itself_passes(self, clean_env):
        clean_env.setenv("OLLAMA_PRIMARY_MODEL", "qwen2.5:7b")
        assert model_for("recruitment_email_primary") == "qwen2.5:7b"


class TestVisionIsNotSubjectToTheTextFloor:
    """Substituting a text model into a vision route would break reading
    entirely, which is worse than the thing the floor prevents."""

    def test_the_vision_route_is_not_a_text_decision_route(self):
        assert "recruitment_document_vision" not in TEXT_DECISION_ROUTES
        assert "payment_screenshot_vision" not in TEXT_DECISION_ROUTES

    def test_a_vision_model_is_never_replaced_by_a_text_model(self, clean_env):
        clean_env.setenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
        assert model_for("recruitment_document_vision") == "qwen2.5vl:7b"

    def test_payment_vision_keeps_its_own_variable(self, clean_env):
        clean_env.setenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
        assert model_for("payment_screenshot_vision") == "qwen3-vl:8b-instruct"


class TestTheVerifiedDefaults:
    """The models checked against what is actually installed on the nodes."""

    @pytest.mark.parametrize("route", [
        "recruitment_email_primary", "recruitment_email_validator", "reasoning_text",
    ])
    def test_text_work_defaults_to_the_primary_text_model(self, clean_env, route):
        assert model_for(route) == "qwen2.5:7b"

    @pytest.mark.parametrize("route", [
        "recruitment_document_vision", "interview_screenshot_vision",
        "payment_screenshot_vision", "resume_vision",
    ])
    def test_visual_work_defaults_to_the_vision_model(self, clean_env, route):
        assert model_for(route) == "qwen3-vl:8b-instruct"


class TestTheGatewayFallbackIsAMailDecisionToo:
    def test_a_weak_fallback_is_refused(self, clean_env):
        from core.ai_gateway import configured_models

        clean_env.setenv("OLLAMA_FALLBACK_MODEL", "gemma2:2b")
        assert configured_models()["fallback"] == SAFE_TEXT_DECISION_MODEL

    def test_a_weak_reasoning_model_cannot_reach_the_fallback(self, clean_env):
        """OLLAMA_REASONING_MODEL is the third source the fallback reads."""
        from core.ai_gateway import configured_models

        clean_env.setenv("OLLAMA_REASONING_MODEL", "gemma2:2b")
        assert configured_models()["fallback"] == SAFE_TEXT_DECISION_MODEL

    def test_the_whole_gateway_view_is_the_verified_pair(self, clean_env):
        from core.ai_gateway import configured_models

        models = configured_models()
        assert models["primary"] == models["validator"] == "qwen2.5:7b"
        assert models["text"] == "qwen2.5:7b"
        assert models["fallback"] == "qwen2.5:7b"
        assert models["vision"] == "qwen3-vl:8b-instruct"
