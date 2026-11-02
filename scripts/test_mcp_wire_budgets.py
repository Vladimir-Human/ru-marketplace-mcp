"""The wire gate must refuse a budget, ceiling or threshold that cannot bound.

R-WIRE-BUDGET-VALIDATION: a baseline budget of 1e999 parses as ``inf``, and an
infinite budget compares against nothing -- the regression delta becomes
``nan``, no comparison exceeds the limit, and the check passes a tool that
grew by about a million tokens. ``True`` is not a number either, although
Python says it is an int, and a string is not a number at all. Every budget,
ceiling and threshold is therefore read as a finite number above zero, and
anything else fails the run with a clear message and a failing exit code.

The tests drive the script's own ``main()`` on temporary baselines with a
faked fetch layer, the way the gate runs in CI, and keep the still-green paths
as positive controls: a measurement run, an explicit baseline creation and the
committed baseline itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import mcp_wire

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_profile_factory(tools: list[dict]):
    """A stand-in for the stdio probe: the tools the test wants measured."""

    def _fake(root, script, timeout):
        return list(tools), "", 0.01

    return _fake


def _baseline(tmp_path: Path, wire_tokens: object, model_tokens: object, ceiling: object) -> Path:
    """A baseline file whose only profile carries the limits under test."""
    target = tmp_path / "baseline.json"
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "compare-mcp": {
                        "script": "compare-mcp",
                        "wire_tokens": wire_tokens,
                        "model_tokens": model_tokens,
                        "latency_ms": 1,
                    }
                },
                "ceilings": {"compare-mcp": ceiling},
            }
        ),
        encoding="utf-8",
    )
    return target


def _run(argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tools: list[dict]) -> int:
    """Run the gate on a temporary baseline with a faked measurement."""
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile_factory(tools))
    return mcp_wire.main(["mcp_wire.py", *argv])


# --- the defects: each of these fails on the pre-fix script ---------------

SMALL_TOOL = [{"name": "tool", "description": "x"}]
GROWN_TOOL = [{"name": "tool", "description": "x" * 4_000_000}]


def test_an_infinite_budget_fails_the_check(tmp_path, monkeypatch, capsys) -> None:
    """1e999 is inf: the delta becomes nan and the gate used to pass anything."""
    baseline = _baseline(tmp_path, 1e999, 1e999, 1e999)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        GROWN_TOOL,
    )

    assert code == 1
    assert "not a finite positive number" in capsys.readouterr().err


def test_a_boolean_budget_fails_the_check(tmp_path, monkeypatch, capsys) -> None:
    """True is not a number, although Python says it is an int."""
    baseline = _baseline(tmp_path, True, True, True)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        GROWN_TOOL,
    )

    assert code == 1
    error = capsys.readouterr().err
    assert "not a finite positive number" in error
    assert "regression" not in error


def test_a_nan_budget_fails_the_check(tmp_path, monkeypatch, capsys) -> None:
    """nan compares false against everything: it cannot be a limit."""
    baseline = _baseline(tmp_path, float("nan"), float("nan"), float("nan"))

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        GROWN_TOOL,
    )

    assert code == 1
    assert "not a finite positive number" in capsys.readouterr().err


def test_a_string_budget_fails_the_check(tmp_path, monkeypatch, capsys) -> None:
    """A quoted number is not a number."""
    baseline = _baseline(tmp_path, "1000", "1000", "1000")

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        GROWN_TOOL,
    )

    assert code == 1
    assert "not a finite positive number" in capsys.readouterr().err


def test_an_infinite_ceiling_fails_the_check(tmp_path, monkeypatch, capsys) -> None:
    """A ceiling of inf bounds nothing, so the check must not trust it."""
    baseline = _baseline(tmp_path, 10_000_000, 10_000_000, 1e999)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        GROWN_TOOL,
    )

    assert code == 1
    assert "not a finite positive number" in capsys.readouterr().err


def test_the_gate_helper_itself_refuses_an_unusable_budget() -> None:
    """The helper must be safe on its own, not only through main()."""
    snapshot = mcp_wire._snapshot("compare-mcp", [{"name": "tool", "description": "x"}], 0.01)
    baseline = {
        "profiles": {"compare-mcp": {"wire_tokens": float("inf"), "model_tokens": 10, "latency_ms": 1}},
        "ceilings": {"compare-mcp": 10},
    }

    gate = mcp_wire._check_gate(snapshot, baseline, max_regression=10.0, max_latency_ms=None, gate=True)

    assert gate["ok"] is False
    assert any("invalid_token_budget" in failure for failure in gate["failures"])


def test_a_non_finite_threshold_option_fails_the_run(tmp_path, monkeypatch, capsys) -> None:
    """A nan threshold never fires: the gate would pass every snapshot."""
    baseline = _baseline(tmp_path, 10_000_000, 10_000_000, 10_000_000)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "nan"],
        tmp_path,
        monkeypatch,
        SMALL_TOOL,
    )

    assert code == 1
    assert "--max-token-regression-percent" in capsys.readouterr().err


def test_an_infinite_threshold_option_fails_the_run(tmp_path, monkeypatch, capsys) -> None:
    baseline = _baseline(tmp_path, 10_000_000, 10_000_000, 10_000_000)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "inf"],
        tmp_path,
        monkeypatch,
        SMALL_TOOL,
    )

    assert code == 1
    assert "--max-token-regression-percent" in capsys.readouterr().err


def test_a_negative_threshold_option_fails_the_run(tmp_path, monkeypatch, capsys) -> None:
    baseline = _baseline(tmp_path, 10_000_000, 10_000_000, 10_000_000)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "-5"],
        tmp_path,
        monkeypatch,
        SMALL_TOOL,
    )

    assert code == 1
    assert "--max-token-regression-percent" in capsys.readouterr().err


def test_a_non_numeric_threshold_option_fails_the_run(tmp_path, monkeypatch, capsys) -> None:
    baseline = _baseline(tmp_path, 10_000_000, 10_000_000, 10_000_000)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "ten"],
        tmp_path,
        monkeypatch,
        SMALL_TOOL,
    )

    assert code == 1
    assert "--max-token-regression-percent" in capsys.readouterr().err


# --- the positive controls: the valid cases still work --------------------


def test_a_measurement_run_without_a_baseline_still_works(tmp_path, monkeypatch) -> None:
    """A plain measurement compares against nothing and stays permissive."""
    code = _run(["compare-mcp"], tmp_path, monkeypatch, SMALL_TOOL)

    assert code == 0


def test_the_explicit_baseline_creation_still_works(tmp_path, monkeypatch) -> None:
    """--update-baseline is the documented way to create a baseline."""
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile_factory(SMALL_TOOL))

    code = mcp_wire.main(["mcp_wire.py", "compare-mcp", "--baseline", str(target), "--update-baseline"])

    assert code == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["ceilings"]["compare-mcp"] > 0
    assert written["profiles"]["compare-mcp"]["wire_tokens"] > 0


def test_a_valid_threshold_option_still_works(tmp_path, monkeypatch) -> None:
    """A finite positive threshold is accepted, as before."""
    baseline = _baseline(tmp_path, 10_000_000, 10_000_000, 10_000_000)

    code = _run(
        ["compare-mcp", "--baseline", str(baseline), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        SMALL_TOOL,
    )

    assert code == 0


def test_a_missing_budget_still_fails_the_check(tmp_path, monkeypatch, capsys) -> None:
    """The fail-closed rule of the earlier fix keeps working."""
    target = tmp_path / "baseline.json"
    target.write_text(
        json.dumps({"version": 1, "profiles": {"compare-mcp": {"latency_ms": 1}}, "ceilings": {}}),
        encoding="utf-8",
    )

    code = _run(
        ["compare-mcp", "--baseline", str(target), "--max-token-regression-percent", "10"],
        tmp_path,
        monkeypatch,
        SMALL_TOOL,
    )

    assert code == 1
    assert "missing_token_budget" in capsys.readouterr().err


def test_the_committed_baseline_passes_the_gate(tmp_path, monkeypatch) -> None:
    """The real baseline of the repository is valid and stays green."""
    committed = REPO_ROOT / "work" / "performance" / "wire-baseline.json"
    monkeypatch.setattr(mcp_wire, "fetch_profile", _fake_profile_factory(SMALL_TOOL))

    code = mcp_wire.main(
        [
            "mcp_wire.py",
            "compare-mcp",
            "decision-mcp",
            "marketplace-mcp",
            "--baseline",
            str(committed),
            "--max-token-regression-percent",
            "10",
        ]
    )

    assert code == 0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
