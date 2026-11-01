from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mcp_wire
from mcp_wire import _check_gate, _snapshot
from model_routing_eval import evaluate as evaluate_model_routing
from routing_eval import evaluate, route


def _fake_profile(root, script, timeout):
    """A stand-in for the stdio probe: one tool, one instruction line."""
    return [{"name": "tool"}], "Use the guide resources.", 0.01


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


def test_wire_gate_fails_when_a_checked_profile_is_not_in_the_baseline() -> None:
    # Gate mode (a baseline comparison without --update-baseline) must fail
    # closed: a checked profile with no baseline entry has nothing to compare
    # against, and the pre-fix gate passed any snapshot, however large.
    snapshot = _snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01)
    gate = _check_gate(snapshot, {"profiles": {}}, max_regression=10.0, max_latency_ms=None, gate=True)
    assert gate["ok"] is False
    assert any("missing_baseline_profile" in item for item in gate["failures"])


def test_wire_gate_fails_when_a_checked_profile_has_no_token_budget() -> None:
    snapshot = _snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01)
    baseline = {"profiles": {"compare-mcp": {"latency_ms": 1}}}
    gate = _check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None, gate=True)
    assert gate["ok"] is False
    assert any("missing_token_budget" in item for item in gate["failures"])


def test_wire_gate_stays_green_against_a_complete_baseline() -> None:
    # The normal regression check must keep working: a checked profile with a
    # current budget and no regression is still ok in gate mode.
    # A complete baseline now also carries an absolute ceiling (R-WIRE-MEASUREMENT):
    # without one, gate mode fails closed on purpose.
    snapshot = _snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01)
    baseline = {
        "profiles": {"compare-mcp": {"wire_tokens": snapshot["wire_tokens"], "latency_ms": 1}},
        "ceilings": {"compare-mcp": mcp_wire.total_tokens(snapshot)},
    }
    gate = _check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None, gate=True)
    assert gate["ok"] is True


def test_wire_gate_fails_when_the_baseline_file_is_missing(tmp_path, monkeypatch, capsys) -> None:
    # A missing baseline file in gate mode used to load as an empty baseline,
    # so the CI check passed with nothing compared at all.
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile)
    code = mcp_wire.main(["mcp_wire.py", "compare-mcp", "--baseline", str(tmp_path / "absent.json")])
    assert code == 1
    assert "absent.json" in capsys.readouterr().err


def test_wire_gate_fails_when_the_baseline_file_is_unreadable(tmp_path, monkeypatch, capsys) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile)
    code = mcp_wire.main(["mcp_wire.py", "compare-mcp", "--baseline", str(broken)])
    assert code == 1
    assert "cannot read baseline file" in capsys.readouterr().err


def test_wire_update_baseline_still_creates_a_missing_baseline(tmp_path, monkeypatch) -> None:
    # Only gate mode fails on a missing baseline; --update-baseline is the
    # documented way to create one, so it must keep working.
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile)
    code = mcp_wire.main(["mcp_wire.py", "compare-mcp", "--baseline", str(target), "--update-baseline"])
    assert code == 0
    assert target.exists()


def test_wire_snapshot_counts_initialize_instructions() -> None:
    """Instructions are paid once per session, so they are measured, not assumed free."""
    snapshot = _snapshot("demo", [{"name": "tool"}], 0.01, "Use the guide resources.")

    assert snapshot["instructions_tokens"] == mcp_wire.estimate_tokens("Use the guide resources.")
    without = _snapshot("demo", [{"name": "tool"}], 0.01)
    assert without["instructions_tokens"] == 0
    assert mcp_wire.total_tokens(snapshot) == snapshot["wire_tokens"] + snapshot["instructions_tokens"]


def test_wire_gate_fails_on_the_absolute_ceiling() -> None:
    """The relative gate only sees a change since the last hand-written baseline."""
    snapshot = _snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01)
    baseline = {
        "profiles": {"compare-mcp": {"wire_tokens": snapshot["wire_tokens"], "latency_ms": 1}},
        "ceilings": {"compare-mcp": snapshot["wire_tokens"] - 1},
    }
    gate = _check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None)

    assert gate["ok"] is False
    assert "ceiling_exceeded:" in " ".join(gate["failures"])


def test_wire_gate_passes_under_the_ceiling() -> None:
    snapshot = _snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01, "instructions")
    baseline = {
        "profiles": {"compare-mcp": {"wire_tokens": snapshot["wire_tokens"], "latency_ms": 1}},
        "ceilings": {"compare-mcp": mcp_wire.total_tokens(snapshot)},
    }
    gate = _check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None, gate=True)

    assert gate["ok"] is True
    assert gate["total_tokens"] == mcp_wire.total_tokens(snapshot)


def test_wire_gate_fails_when_a_checked_profile_has_no_ceiling() -> None:
    snapshot = _snapshot("compare-mcp", [{"name": "tool"}], 0.01)
    baseline = {
        "profiles": {"compare-mcp": {"wire_tokens": snapshot["wire_tokens"], "latency_ms": 1}},
        "ceilings": {},
    }
    gate = _check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None, gate=True)

    assert gate["ok"] is False
    assert any("missing_ceiling" in item for item in gate["failures"])


def test_update_baseline_records_a_ceiling_and_keeps_one_already_set(tmp_path, monkeypatch) -> None:
    """Raising a ceiling is a decision: measuring must not undo it."""
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile)
    assert mcp_wire.main(["mcp_wire.py", "compare-mcp", "--baseline", str(target), "--update-baseline"]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["ceilings"]["compare-mcp"] > 0
    assert "instructions_tokens" in written["profiles"]["compare-mcp"]

    written["ceilings"]["compare-mcp"] = 4242
    target.write_text(json.dumps(written), encoding="utf-8")
    assert mcp_wire.main(["mcp_wire.py", "compare-mcp", "--baseline", str(target), "--update-baseline"]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["ceilings"]["compare-mcp"] == 4242


def test_model_routing_matrix_is_complete() -> None:
    report = evaluate_model_routing()
    assert report["ok"] is True


if __name__ == "__main__":
    # The documented invocation is `uv run python scripts/test_ops_gates.py`
    # (README, Development section). Without this runner that command exits 0
    # having collected nothing — a vacuous gate, the same bug class as a check
    # that cannot fail. pytest.main runs the tests above and propagates
    # the real exit code; `uv run pytest scripts/test_ops_gates.py` keeps
    # working unchanged.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
