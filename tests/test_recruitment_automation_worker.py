from workers.recruitment_mail_worker import RecruitmentMailWorker
from workers import recruitment_mail_worker as worker_module


def test_legacy_review_promotion_runs_even_when_ollama_is_down(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_module.store, "promote_legacy_review_states", lambda: calls.append("retry") or 2)
    monkeypatch.setattr(worker_module.store, "promote_ignored_messages", lambda: calls.append("ignore") or 3)
    monkeypatch.setattr(worker_module, "_publish", lambda *_args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr("core.ai_gateway.health", lambda **_kwargs: {"endpoint_reachable": False, "model_available": False})

    RecruitmentMailWorker().process_ai_recovery()

    assert calls[:2] == ["retry", "ignore"]
    assert calls[2]["promoted_retry_count"] == 2
    assert calls[2]["auto_ignore_count"] == 3
