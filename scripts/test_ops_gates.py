from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_wire import _check_gate, _snapshot
from model_routing_eval import evaluate as evaluate_model_routing
from routing_eval import evaluate, route


def test_routing_fixture_covers_positive_and_negative_cases() -> None:
    report = evaluate()
    assert report["ok"] is True
    assert report["accuracy"] == 1.0
    assert route("где дешевле купить чайник") == "compare-prices"
    assert route("отзывы на озоне") == "ozon-connector"
    assert route("все источники") == "marketplace"


def test_wire_gate_fails_on_token_regression() -> None:
    snapshot = _snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01)
    baseline = {"profiles": {"compare-mcp": {"wire_tokens": 1, "latency_ms": 1}}}
    gate = _check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None)
    assert gate["ok"] is False
    assert any("wire_tokens_regression" in item for item in gate["failures"])


def test_wire_snapshot_is_machine_readable() -> None:
    snapshot = _snapshot("demo", [{"name": "b"}, {"name": "a"}], 0.1234)
    assert snapshot["tool_count"] == 2
    assert snapshot["tools"][0]["name"] == "a"
    json.dumps(snapshot, ensure_ascii=False)


def test_model_routing_matrix_is_complete() -> None:
    report = evaluate_model_routing()
    assert report["ok"] is True


if __name__ == "__main__":
    # The documented invocation is `uv run python scripts/test_ops_gates.py`
    # (README, Development section). Without this runner that command exits 0
    # having collected nothing — a vacuous gate, the same bug class as a check
    # that cannot fail. pytest.main runs the three tests above and propagates
    # the real exit code; `uv run pytest scripts/test_ops_gates.py` keeps
    # working unchanged.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
