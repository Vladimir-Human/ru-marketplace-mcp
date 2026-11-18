"""The first-run probe stays off the owner's Chrome, profile and config, and reports what it measured.

START_MODULE_CONTRACT
PURPOSE: Prove R-FIRST-RUN-PROBE offline: the step order, the isolation settings, the doctor-row parser,
         the step recorder (exit code, seconds, timeout), --dry-run cleanup, finally Chrome close on failure,
         and port-close wait + transient PermissionError rmtree retry on Windows.
SCOPE: scripts/first_run_probe.py; real subprocesses of this Python and loopback sockets only; no uvx, no Chrome, no network.
DEPENDS: pytest; stdlib json, shutil, socket, sys, threading, time; first_run_probe.
LINKS: M-FIRST-RUN-PROBE; R-FIRST-RUN-PROBE.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
import time
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
    with pytest.raises(ValueError, match="9222"):
        probe.wait_port_closed(9222)


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


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_chrome_is_closed_and_work_removed_when_a_step_raises(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path_factory.mktemp("outside-raise")
    monkeypatch.setattr(probe, "inside_checkout", lambda path: None)
    closed_ports: list[int] = []

    def fake_close_chrome(port: int) -> bool:
        closed_ports.append(port)
        return True

    def crashing_run_steps(steps: list[probe.Step], env: dict[str, str]) -> list[dict[str, object]]:
        profile = Path(env["CHROME_SCRAPING_PROFILE"])
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "Crashpad").write_text("active", encoding="utf-8")
        raise RuntimeError("mid-probe failure")

    monkeypatch.setattr(probe, "close_chrome", fake_close_chrome)
    monkeypatch.setattr(probe, "run_steps", crashing_run_steps)

    with pytest.raises(RuntimeError, match="mid-probe failure"):
        probe.main(["--from", "ru-marketplace-mcp==9.9.9", "--work-root", str(root)])

    assert len(closed_ports) == 1 and closed_ports[0] != 9222
    assert list(root.iterdir()) == []


@pytest.mark.req("R-FIRST-RUN-PROBE")
def test_cleanup_waits_for_port_and_retries_transient_permission_error(
    tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path_factory.mktemp("outside-retry")
    monkeypatch.setattr(probe, "inside_checkout", lambda path: None)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    assert port != 9222

    port_closed_at: list[float] = []

    def delayed_listener_close() -> None:
        time.sleep(0.15)
        port_closed_at.append(time.monotonic())
        listener.close()

    closer = threading.Thread(target=delayed_listener_close, daemon=True)
    monkeypatch.setattr(probe, "free_port", lambda: port)
    monkeypatch.setattr(probe, "run_steps", lambda steps, env: [])

    def fake_close_chrome(target_port: int) -> bool:
        assert target_port == port
        closer.start()
        return True

    monkeypatch.setattr(probe, "close_chrome", fake_close_chrome)

    real_rmtree = shutil.rmtree
    rmtree_calls: list[tuple[float, bool]] = []

    def flaky_windows_rmtree(path: str | Path, ignore_errors: bool = False) -> None:
        rmtree_calls.append((time.monotonic(), ignore_errors))
        if len(rmtree_calls) <= 2:
            if ignore_errors:
                return
            raise PermissionError(13, "Crashpad still holds profile lock", str(path))
        real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(probe.shutil, "rmtree", flaky_windows_rmtree)

    try:
        assert probe.main(["--from", "ru-marketplace-mcp==9.9.9", "--skip-live", "--work-root", str(root)]) == 0
    finally:
        closer.join(timeout=2.0)
        listener.close()

    report = json.loads(capsys.readouterr().out)
    assert report["chrome_closed"] is True
    assert len(rmtree_calls) == 3
    assert port_closed_at and rmtree_calls[0][0] >= port_closed_at[0]
    assert list(root.iterdir()) == []

    # Bounded fallback when a lock outlives the retry window: falls back to ignore_errors=True without raising.
    stuck = root / "stuck"
    stuck.mkdir()
    fallback_flags: list[bool] = []

    def permanently_locked_rmtree(path: str | Path, ignore_errors: bool = False) -> None:
        fallback_flags.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError(13, "still locked", str(path))
        real_rmtree(path, ignore_errors=True)

    monkeypatch.setattr(probe.shutil, "rmtree", permanently_locked_rmtree)
    probe.remove_work_dir(stuck, timeout_s=0.05, initial_delay_s=0.02)
    assert fallback_flags[-1] is True and False in fallback_flags
    assert not stuck.exists()
