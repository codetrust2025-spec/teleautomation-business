import json

import pytest

from core import ollama_nodes


def test_jagadeesh_is_default_primary(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "nodes.json"))
    monkeypatch.delenv("OLLAMA_PRIMARY_NODE", raising=False)
    assert ollama_nodes.primary_node_id() == "jagadeesh"
    assert ollama_nodes.primary_base_url() == "http://127.0.0.1:11435"


def test_primary_selection_is_persisted(monkeypatch, tmp_path):
    state = tmp_path / "nodes.json"
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(state))
    # force=True because this covers persistence, not the readiness check —
    # that gate is exercised in test_ollama_pool_failover.py and would
    # otherwise need a live node to answer.
    assert ollama_nodes.set_primary_node("our_machine", force=True) == "our_machine"
    assert json.loads(state.read_text(encoding="utf-8")) == {
        "primary_node": "our_machine"
    }
    assert ollama_nodes.primary_node_id() == "our_machine"


def test_unknown_node_cannot_be_selected(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "nodes.json"))
    with pytest.raises(ValueError, match="Unknown Ollama node"):
        ollama_nodes.set_primary_node("unknown")


def test_node_health_reports_model_and_loaded_state(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "nodes.json"))

    def respond(node_id, path, **_kwargs):
        assert node_id == "jagadeesh"
        if path == "/api/tags":
            return {"models": [{"name": "qwen3-vl:8b-instruct"}]}
        return {"models": [{"name": "qwen3-vl:8b-instruct"}]}

    monkeypatch.setattr(ollama_nodes, "_request", respond)
    status = ollama_nodes.node_health(
        "jagadeesh", model="qwen3-vl:8b-instruct"
    )
    assert status["status"] == "online"
    assert status["model_available"] is True
    assert status["model_loaded"] is True
    assert status["primary"] is True


def test_unload_uses_keep_alive_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "nodes.json"))
    calls = []
    health_results = iter(
        [
            {
                "endpoint_reachable": True,
                "model_available": True,
                "model_loaded": True,
            },
            {
                "endpoint_reachable": True,
                "model_available": True,
                "model_loaded": False,
            },
        ]
    )
    monkeypatch.setattr(
        ollama_nodes, "node_health", lambda *args, **kwargs: next(health_results)
    )
    monkeypatch.setattr(
        ollama_nodes,
        "_request",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {},
    )
    result = ollama_nodes.unload_model(
        "jagadeesh", model="qwen3-vl:8b-instruct"
    )
    assert result["unloaded"] is True
    assert calls[0][0][1] == "/api/generate"
    assert calls[0][1]["payload"] == {
        "model": "qwen3-vl:8b-instruct",
        "keep_alive": 0,
    }
