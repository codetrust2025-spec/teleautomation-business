from pathlib import Path


def test_only_shared_gateway_calls_ollama_http_endpoints():
    root = Path(__file__).resolve().parents[1]
    source_roots = ("core", "features", "services", "workers")
    offenders = []
    for source_root in source_roots:
        for path in (root / source_root).rglob("*.py"):
            # ollama_nodes.py is the intentionally shared transport for
            # per-node health and unload administration. Workload inference
            # remains centralized in ai_gateway.py.
            if path.name in {"ai_gateway.py", "ollama_nodes.py"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "/api/chat" in text or "/api/tags" in text:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
