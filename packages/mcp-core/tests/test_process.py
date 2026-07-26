"""Tests for ``mcp_core.process`` — worker isolation and process-tree teardown.

These live here rather than in a connector's suite because that is what they
test: the shared process primitives, not any marketplace. They arrived with the
Ozon connector (its tier-1 fetch runs in a subprocess) and moved here once the
same primitives became shared runtime.

Windows-specific branches are exercised on every platform through
``process.PLATFORM_OVERRIDE``, so Linux CI still covers the code paths only
Windows would take at runtime.
"""

from __future__ import annotations

import os

import pytest
from mcp_core import process


class _FakeProc:
    """Minimal Popen stand-in that records how it was torn down."""

    def __init__(self, captured: dict, pid: int = 12345) -> None:
        self.pid = pid
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self._captured = captured

    def poll(self):
        return None

    def kill(self):
        self._captured["killed"] = True

    def wait(self, timeout=None):
        self._captured["wait_timeout"] = timeout


def test_safe_child_env_does_not_case_fold_on_posix(monkeypatch):
    """A lowercase 'path' must not slip through the POSIX allowlist.

    Case-folding is correct on Windows (env keys are case-insensitive there) and
    wrong on POSIX, where 'path' and 'PATH' are different variables.
    """
    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    monkeypatch.setitem(os.environ, "path", "lowercase-secret")

    assert process.safe_child_env().get("path") is None


def test_safe_child_env_case_folds_on_windows(monkeypatch):
    """Under the Windows rule, a differently-cased allowlist key still passes.

    Windows environment keys are case-insensitive, so 'LocalAppData' and
    'LOCALAPPDATA' are the same variable and both belong in a child environment.
    POSIX keys are matched verbatim (covered by the test above).

    The environment is injected as a plain mixed-case dict rather than through
    monkeypatch.setitem(os.environ, ...): real os.environ on Windows upper-cases
    keys on write, so a mixed-case probe set that way would be silently folded
    to LOCALAPPDATA before safe_child_env ever sees it, making the case-fold
    rule unobservable. The dict keeps the mixed case the function must handle.
    """
    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "nt")
    monkeypatch.setattr(
        process.os,
        "environ",
        {
            "LocalAppData": r"C:\Users\op\AppData\Local",
            "SystemRoot": r"C:\Windows",
        },
    )

    env = process.safe_child_env()

    assert env.get("LocalAppData") == r"C:\Users\op\AppData\Local"
    assert env.get("SystemRoot") == r"C:\Windows"


def test_safe_child_env_excludes_secrets_on_every_platform(monkeypatch):
    monkeypatch.setitem(os.environ, "OZON_SENTINEL_SECRET", "leak")
    monkeypatch.setitem(os.environ, "AWS_SECRET_ACCESS_KEY", "leak")

    env = process.safe_child_env()

    assert "OZON_SENTINEL_SECRET" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_worker_process_kwargs_per_platform(monkeypatch):
    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    assert process.worker_process_kwargs() == {"start_new_session": True}

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "nt")
    kwargs = process.worker_process_kwargs()
    assert "creationflags" in kwargs
    assert kwargs["creationflags"] == 0x00000200 | 0x08000000


def test_terminate_worker_tree_uses_absolute_taskkill_and_sanitized_env(monkeypatch):
    """On Windows: kill the whole tree via an absolute, un-hijackable taskkill.

    Runs on every platform via PLATFORM_OVERRIDE, so the Windows contract is
    covered by CI on Linux and macOS too.
    """
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "nt")
    monkeypatch.setenv("OZON_SENTINEL_SECRET", "leak")
    # A hijacked environment must not be able to redirect taskkill.
    monkeypatch.setenv("SystemRoot", r"C:\attacker")
    monkeypatch.setenv("WINDIR", r"C:\attacker")
    monkeypatch.setattr(process.subprocess, "run", fake_run)

    process.terminate_process_tree(_FakeProc(captured))

    assert captured["argv"][0].lower().endswith(r"\system32\taskkill.exe")
    assert not captured["argv"][0].lower().startswith(r"c:\attacker")
    assert captured["argv"][1:] == ["/PID", "12345", "/T", "/F"]
    assert "OZON_SENTINEL_SECRET" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["env"]["SystemRoot"].lower() != r"c:\attacker"
    assert captured["wait_timeout"] == 0.5


def test_terminate_worker_tree_kills_process_group_on_posix(monkeypatch):
    """On POSIX: SIGKILL the child's process group, never a bare kill()."""
    captured: dict = {}
    killed: dict = {}

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    # Patch the connector's own seam rather than os.getpgid/os.killpg: those do
    # not exist on Windows, so patching them made this test unrunnable there.
    monkeypatch.setattr(process, "kill_process_group", lambda pid: killed.update(pid=pid))

    process.terminate_process_tree(_FakeProc(captured))

    assert killed["pid"] == 12345
    assert "killed" not in captured  # the group kill succeeded, so no fallback
    assert captured["wait_timeout"] == 0.5


def test_terminate_worker_tree_falls_back_to_kill_when_killpg_fails(monkeypatch):
    """A failed killpg must degrade to proc.kill(), never propagate."""
    captured: dict = {}

    def boom(_pid):
        raise ProcessLookupError("gone")

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    monkeypatch.setattr(process, "kill_process_group", boom)

    process.terminate_process_tree(_FakeProc(captured))

    assert captured.get("killed") is True


def test_kill_process_group_uses_posix_signalling(monkeypatch):
    """The POSIX path signals the whole group, not just the child pid."""
    calls: dict = {}

    monkeypatch.setattr(process.os, "getpgid", lambda pid: pid + 1000, raising=False)
    monkeypatch.setattr(process.os, "killpg", lambda pgid, sig: calls.update(pgid=pgid, sig=sig), raising=False)
    monkeypatch.setattr(process.signal, "SIGKILL", 9, raising=False)

    process.kill_process_group(42)

    assert calls == {"pgid": 1042, "sig": 9}


def test_kill_process_group_refuses_where_process_groups_do_not_exist(monkeypatch):
    """On Windows os.killpg simply does not exist, so this must raise cleanly.

    terminate_process_tree turns that into a proc.kill() fallback; what matters
    here is that it raises AttributeError rather than crashing on a missing name.
    """
    monkeypatch.delattr(process.os, "killpg", raising=False)

    with pytest.raises(AttributeError):
        process.kill_process_group(42)


def test_proxy_env_vars_are_excluded_from_the_worker_environment(monkeypatch):
    """The child env allowlist must not leak proxy configuration."""
    monkeypatch.setenv("HTTPS_PROXY", "http://leaky:8080")
    monkeypatch.setenv("ALL_PROXY", "http://leaky:8080")
    child_env = process.safe_child_env()
    assert "HTTPS_PROXY" not in child_env
    assert "ALL_PROXY" not in child_env
