"""The first-run probe stays off the owner's Chrome, profile and config, and reports what it measured.

START_MODULE_CONTRACT
PURPOSE: Prove R-FIRST-RUN-PROBE offline: the step order, the isolation settings, the doctor-row parser,
         the step recorder (exit code, seconds, timeout) and --dry-run cleanup.
SCOPE: scripts/first_run_probe.py; real subprocesses of this Python only; no uvx, no Chrome, no network.
DEPENDS: pytest; stdlib json, sys; first_run_probe.
LINKS: M-FIRST-RUN-PROBE; R-FIRST-RUN-PROBE.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import first_run_probe as probe


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_the_steps_run_setup_install_doctor_and_matrix_in_order(tmp_path: Path) -> None:
    steps = probe.plan_steps("ru-marketplace-mcp==9.9.9", tmp_path, "python", skip_live=False)

    assert [step.name for step in steps] == ["setup", "install claude-code", "doctor", "matrix"]
    assert steps[0].argv[:4] == ["uvx", "--from", "ru-marketplace-mcp==9.9.9", "marketplace-mcp"]
    assert "--no-doctor" in steps[0].argv
    assert [step.cwd for step in steps[:2]] == [tmp_path, tmp_path]  # never inside the checkout
    assert steps[2].cwd == probe.REPO_ROOT
    assert [step.live for step in steps] == [False, False, True, True]
    assert steps[2].argv[-2:] == ["--status-file", str(tmp_path / "doctor.json")]
    assert all("live_check.py" in step.argv[1] for step in steps[2:])
    assert [s.name for s in probe.plan_steps("x", tmp_path, "python", skip_live=True)] == [
        "setup",
        "install claude-code",
    ]


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_the_settings_keep_every_step_off_the_owner_chrome_profile_and_config(tmp_path: Path) -> None:
    port = probe.free_port()
    env = probe.probe_env(tmp_path, port, warm_cache=False)

    assert port != 9222 and env["CHROME_CDP_PORT"] == str(port)
    for key in ("CHROME_SCRAPING_PROFILE", "MARKETPLACE_MCP_CLAUDE_CONFIG", "UV_CACHE_DIR"):
        assert Path(env[key]).parent == tmp_path, key
    assert env["CHROME_CHALLENGE_HANDOFF_S"] == "0"
    assert "UV_CACHE_DIR" not in probe.probe_env(tmp_path, port, warm_cache=True)
    with pytest.raises(ValueError, match="9222"):
        probe.probe_env(tmp_path, 9222, warm_cache=False)
    with pytest.raises(ValueError, match="9222"):
        probe.close_chrome(9222)


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_doctor_rows_list_each_source_state_and_next_step() -> None:
    status = {
        "healthy": ["ozon"],
        "sources": {
            "ozon": {"status": "healthy", "detail": "ok", "next_step": None},
            "lamoda": {"status": "blocked", "detail": "403", "next_step": "marketplace-mcp unblock lamoda"},
            "odd": "not an object",
        },
    }

    assert probe.doctor_rows(status) == [
        {"source": "lamoda", "state": "blocked", "next_step": "marketplace-mcp unblock lamoda"},
        {"source": "odd", "state": None, "next_step": None},
        {"source": "ozon", "state": "healthy", "next_step": None},
    ]
    assert probe.doctor_rows({}) == []


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_each_step_records_its_exit_seconds_and_output_tail_and_a_timeout_does_not_stop_the_rest() -> None:
    steps = [
        probe.Step("ok", [sys.executable, "-c", "print('first'); print('done')"], 60),
        probe.Step("fails", [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"], 60),
        probe.Step("hangs", [sys.executable, "-c", "import time; time.sleep(30)"], 1),
        probe.Step("missing", ["no-such-command-for-the-probe"], 60),
    ]

    results = probe.run_steps(steps, {})

    assert [(r["step"], r["exit"]) for r in results] == [("ok", 0), ("fails", 3), ("hangs", None), ("missing", None)]
    assert results[0]["tail"][-1] == "done"
    assert "boom" in results[1]["tail"]
    assert results[2]["tail"][0].startswith("timed out after 1 s")
    assert results[3]["tail"][0].startswith("could not start")
    assert all(r["seconds"] >= 0 for r in results)


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_dry_run_prints_the_plan_runs_nothing_and_removes_its_folder(
    tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path_factory.mktemp("outside")
    # The test's own temp folder may sit inside this checkout (TEMP in the repo on Windows); the guard is
    # tested separately below, so here the folder only has to look like it is outside one.
    monkeypatch.setattr(probe, "inside_checkout", lambda path: None)
    assert probe.main(["--from", "ru-marketplace-mcp==9.9.9", "--dry-run", "--work-root", str(root)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert [step["step"] for step in report["steps"]] == ["setup", "install claude-code", "doctor", "matrix"]
    assert report["port"] != 9222
    assert "exit" not in report["steps"][0]
    assert not Path(report["work"]).exists()


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_a_work_folder_inside_a_checkout_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checkout = tmp_path / "clone"
    (checkout / "deep").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = []\n", encoding="utf-8")

    assert probe.inside_checkout(checkout / "deep") == checkout
    assert probe.main(["--from", "x", "--dry-run", "--work-root", str(checkout / "deep")]) == 2
    assert "inside the checkout" in capsys.readouterr().err
    assert list((checkout / "deep").iterdir()) == []
