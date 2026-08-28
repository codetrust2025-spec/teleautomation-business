"""A failed Ollama node must not be handed the same request again.

Before this, node selection happened once, above the retry loop, so every retry
went back to the machine that had just refused the call. The agent-level
fallback then made a fresh `chat_structured` call, which re-selected from
`candidate_order` — a deterministic preference list — and landed on the same
node. The circuit breaker could not help: it needs three consecutive failures
before a 120s cooldown, so it never trips inside a single request.

Selection now happens per attempt against a request-scoped exclusion set.

The distinction that matters is *what a failure says*. A connection refused, a
timeout, a 5xx, a model that will not load: those name the machine, so the
request moves. Malformed JSON or a schema violation name the model and the
prompt — moving would re-run the same prompt against the same weights for the
same bad answer, and cool a node that is working perfectly. Both directions are
asserted here, because getting the second one wrong is how a healthy pool ends
up in cooldown during a bad-prompt incident.
"""

from __future__ import annotations

import pytest

from core import ai_gateway
from core import ollama_nodes


class Recorder:
    """Stands in for the node registry, recording what the gateway did to it."""

    def __init__(self, nodes, failing, *, fail_code="OLLAMA_CONNECTION_FAILED"):
        self.nodes = list(nodes)
        self.failing = set(failing)
        self.fail_code = fail_code
        self.selected = []
        self.failures = []
        self.successes = []
        self.requested = []


@pytest.fixture
def pool(monkeypatch):
    """Three nodes; caller says which of them refuse the request."""

    def install(failing, *, fail_code="OLLAMA_CONNECTION_FAILED", schema_error=False):
        rec = Recorder(["node-a", "node-b", "node-c"], failing, fail_code=fail_code)

        def select(*, model, timeout=5, require_inference=False, exclude=None):
            excluded = {str(n) for n in (exclude or ())}
            for node in rec.nodes:
                if node in excluded:
                    continue
                rec.selected.append(node)
                return {"node_id": node, "base_url": f"http://{node}", "was_primary": False}
            raise RuntimeError("no node left")

        monkeypatch.setattr(ollama_nodes, "candidate_order", lambda model=None: list(rec.nodes))
        monkeypatch.setattr(ollama_nodes, "select_available_node", select)
        monkeypatch.setattr(ollama_nodes, "base_url_for", lambda n: f"http://{n}")
        monkeypatch.setattr(ollama_nodes, "inference_host_id", lambda n: n)
        monkeypatch.setattr(ollama_nodes, "node", lambda n: {"label": n})
        monkeypatch.setattr(ollama_nodes, "primary_node_id", lambda: rec.nodes[0])
        monkeypatch.setattr(ollama_nodes, "record_failure",
                            lambda n, e="": rec.failures.append(n))
        monkeypatch.setattr(ollama_nodes, "record_success", lambda n: rec.successes.append(n))
        monkeypatch.setattr(ai_gateway, "health", lambda **kw: {
            "endpoint_reachable": True, "model_available": True,
            "error_message": "", "error_code": "",
        })
        monkeypatch.setattr(ai_gateway.ollama_status, "record_request_success", lambda *a, **k: None)
        monkeypatch.setattr(ai_gateway.ollama_status, "record_request_failure", lambda *a, **k: None)
        monkeypatch.setattr(ai_gateway.time, "sleep", lambda *_: None)

        def fake_request(path, *, method="POST", body=None, connect_timeout=None,
                         response_timeout=None, base_url=None):
            node = str(base_url).replace("http://", "")
            rec.requested.append(node)
            if node in rec.failing:
                raise ai_gateway.AIGatewayError("node down", code=rec.fail_code)
            if schema_error:
                return {"message": {"content": "not json at all"}}
            return {"message": {"content": '{"ok": true}'}}

        monkeypatch.setattr(ai_gateway, "_request_json", fake_request)
        return rec

    return install


def call(**kw):
    return ai_gateway.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="qwen2.5:7b", max_retries=0, **kw,
    )


class TestFailoverHappens:
    def test_a_failed_node_is_replaced_by_the_next_one(self, pool):
        rec = pool(failing={"node-a"})
        result = call()
        assert result.node_id == "node-b"
        assert rec.requested == ["node-a", "node-b"]

    def test_the_failed_node_is_not_tried_again_in_this_request(self, pool):
        rec = pool(failing={"node-a", "node-b"})
        result = call()
        assert result.node_id == "node-c"
        # Each node is attempted once; none is revisited.
        assert rec.requested == ["node-a", "node-b", "node-c"]
        assert len(set(rec.requested)) == len(rec.requested)

    def test_exclusion_is_passed_to_selection(self, pool):
        rec = pool(failing={"node-a"})
        call()
        # Selection was asked twice and returned different nodes, which only
        # happens if the exclusion set reached it.
        assert rec.selected[:2] == ["node-a", "node-b"]

    @pytest.mark.parametrize("code", sorted(ai_gateway._NODE_FAILURE_CODES))
    def test_every_node_failure_code_fails_over(self, pool, code):
        rec = pool(failing={"node-a"}, fail_code=code)
        assert call().node_id == "node-b"
        assert "node-a" in rec.failures


class TestBreakerBookkeeping:
    def test_a_node_failure_is_recorded_against_that_node(self, pool):
        rec = pool(failing={"node-a"})
        call()
        assert rec.failures == ["node-a"]

    def test_success_is_recorded_against_the_node_that_served_it(self, pool):
        rec = pool(failing={"node-a"})
        call()
        assert rec.successes == ["node-b"]

    def test_a_healthy_first_node_records_no_failure(self, pool):
        rec = pool(failing=set())
        assert call().node_id == "node-a"
        assert rec.failures == []
        assert rec.successes == ["node-a"]


class TestModelOutputDoesNotMoveNodes:
    def test_bad_json_does_not_fail_over(self, pool):
        """The node answered. Another node would give the same bad answer."""
        rec = pool(failing=set(), schema_error=True)
        with pytest.raises(ai_gateway.AIGatewayError) as err:
            call(schema={"type": "object"})
        assert err.value.code == "OLLAMA_INVALID_JSON"
        assert rec.requested == ["node-a"], "a model-output error moved nodes"

    def test_bad_json_does_not_blame_the_node(self, pool):
        rec = pool(failing=set(), schema_error=True)
        with pytest.raises(ai_gateway.AIGatewayError):
            call(schema={"type": "object"})
        assert rec.failures == [], "a working node was cooled for a bad answer"

    def test_the_two_code_sets_do_not_overlap(self):
        """A code in both sets would make the behaviour order-dependent."""
        assert not (ai_gateway._NODE_FAILURE_CODES & ai_gateway._MODEL_OUTPUT_CODES)


class TestExhaustion:
    def test_all_nodes_down_raises_the_real_error(self, pool):
        """Never silently drop the task: the caller gets the transport failure,
        not a generic message that hides what happened."""
        rec = pool(failing={"node-a", "node-b", "node-c"})
        with pytest.raises(ai_gateway.AIGatewayError) as err:
            call()
        assert err.value.code == "OLLAMA_CONNECTION_FAILED"
        assert rec.requested == ["node-a", "node-b", "node-c"]
        assert sorted(rec.failures) == ["node-a", "node-b", "node-c"]

    def test_it_stops_after_trying_each_node_once(self, pool):
        """Without a budget this loops forever once every node is excluded."""
        rec = pool(failing={"node-a", "node-b", "node-c"})
        with pytest.raises(ai_gateway.AIGatewayError):
            call()
        assert len(rec.requested) == 3


class TestUnchangedBehaviour:
    def test_the_preferred_node_is_still_tried_first(self, pool):
        rec = pool(failing=set())
        call()
        assert rec.requested[0] == "node-a"

    def test_a_healthy_pool_makes_exactly_one_request(self, pool):
        rec = pool(failing=set())
        call()
        assert rec.requested == ["node-a"]

    def test_the_result_still_names_the_serving_node(self, pool):
        pool(failing={"node-a"})
        result = call()
        assert result.node_id == "node-b"
        assert result.node_label == "node-b"


class TestSelectionExclusion:
    def test_select_available_node_skips_excluded(self, monkeypatch):
        monkeypatch.setattr(ollama_nodes, "candidate_order", lambda model=None: ["a", "b"])
        monkeypatch.setattr(ollama_nodes, "in_cooldown", lambda n: False)
        monkeypatch.setattr(ollama_nodes, "node_health", lambda n, **kw: {
            "endpoint_reachable": True, "model_available": True,
        })
        monkeypatch.setattr(ollama_nodes, "base_url_for", lambda n: f"http://{n}")
        monkeypatch.setattr(ollama_nodes, "primary_node_id", lambda: "a")
        monkeypatch.setattr(ollama_nodes, "record_success", lambda n: None)
        chosen = ollama_nodes.select_available_node(model="m", exclude={"a"})
        assert chosen["node_id"] == "b"

    def test_excluding_everything_raises_rather_than_looping(self, monkeypatch):
        monkeypatch.setattr(ollama_nodes, "candidate_order", lambda model=None: ["a", "b"])
        with pytest.raises(RuntimeError, match="No Ollama node is left"):
            ollama_nodes.select_available_node(model="m", exclude={"a", "b"})
