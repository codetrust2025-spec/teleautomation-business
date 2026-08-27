"""No AI workload may resolve to moondream.

moondream was the project's vision model and could not read the long
identifiers the payment authorisation depends on: given a PhonePe receipt it
returned `{"transaction_id": "", "utr": "", "amount": 0}` while qwen2.5vl:7b and
qwen3-vl:8b-instruct both read every field correctly. Because a blank result is
indistinguishable from an unreadable image, the failure was silent.

It has been removed from the project. These tests keep it out: a default, an
untouched route, or a stray fallback that reintroduces it fails here rather than
in production, where the symptom is an empty field rather than an error.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from core.ai_model_routing import AUXILIARY_MODEL_ROUTES, MODEL_ROUTES, model_for

ALL_ROUTES = {**MODEL_ROUTES, **AUXILIARY_MODEL_ROUTES}
BANNED = "moondream"


@pytest.mark.parametrize("route", sorted(ALL_ROUTES))
def test_no_route_defaults_to_the_removed_model(route):
    _, default = ALL_ROUTES[route]
    assert BANNED not in default.lower(), f"{route} still defaults to {default}"


@pytest.mark.parametrize("route", sorted(ALL_ROUTES))
def test_no_route_resolves_to_the_removed_model(route, monkeypatch):
    """With no environment overrides, resolution must never land on it."""
    for variable, _ in ALL_ROUTES.values():
        monkeypatch.delenv(variable, raising=False)
    for legacy in ("OLLAMA_MAIL_MODEL", "AI_RECRUITMENT_MODEL"):
        monkeypatch.delenv(legacy, raising=False)
    assert BANNED not in model_for(route).lower()


@pytest.mark.parametrize(
    "module_name",
    ["features.ollama_payment_extract", "features.ollama_invite_extract"],
)
def test_vision_fallbacks_do_not_reintroduce_it(module_name, monkeypatch):
    """The backup model is the easiest place for it to survive: it only runs
    after a primary failure, so a bad value there is rarely observed."""
    for variable in (
        "OLLAMA_BACKUP_VISION_MODEL",
        "OLLAMA_PAYMENT_BACKUP_VISION_MODEL",
        "OLLAMA_VISION_MODEL",
        "OLLAMA_PAYMENT_VISION_MODEL",
    ):
        monkeypatch.delenv(variable, raising=False)
    module = importlib.reload(importlib.import_module(module_name))
    try:
        backup = getattr(module, "OLLAMA_BACKUP_VISION_MODEL", "")
        assert BANNED not in str(backup).lower(), f"{module_name} falls back to {backup}"
    finally:
        monkeypatch.undo()
        importlib.reload(module)


def test_it_is_not_named_as_a_default_in_any_module_source():
    """A hard-coded default hidden in an os.environ.get call would pass the
    route checks above while still reaching production."""
    import features.ollama_invite_extract as invite
    import features.ollama_payment_extract as payment

    for module in (invite, payment):
        source = inspect.getsource(module)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            assert BANNED not in stripped.lower(), (
                f"{module.__name__} still references {BANNED}: {stripped[:90]}"
            )
