"""Explicit OFF-mode coverage for legacy keyword decision regressions."""
import pytest


@pytest.fixture(autouse=True)
def legacy_rules_first(monkeypatch):
    from services import recruitment_mail_agent
    monkeypatch.setattr(recruitment_mail_agent, "pure_ollama_enabled", lambda: False)
