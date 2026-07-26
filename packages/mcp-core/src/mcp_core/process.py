"""Cross-platform helpers for spawning and reaping short-lived worker processes.

The Ozon connector runs blocking ``curl_cffi`` calls in a throwaway child
process so a hung TLS handshake can never wedge the event loop. Killing that
child reliably is platform-specific: Windows needs ``taskkill /T`` to take the
whole tree down, POSIX needs ``killpg`` against a fresh process group. Both
paths live here so connectors stay platform-agnostic and so the behaviour is
unit-testable on any OS via ``PLATFORM_OVERRIDE``.

Child environments are allowlisted rather than inherited: a scraping worker has
no business seeing tokens that happen to sit in the parent environment.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import PureWindowsPath
from typing import Any

# Windows creation flags, defined literally so this module imports cleanly on
# POSIX where the subprocess constants do not exist.
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

# Only these variables reach a child. Python/TLS need a few; the rest is noise
# at best and credential leakage at worst.
CHILD_ENV_ALLOWLIST = frozenset(
    {
        # POSIX + generic
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "TZ",
        # macOS
        "DYLD_LIBRARY_PATH",
        # Windows
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)

# Tests set this to "nt" or "posix" to exercise the other platform's branch.
PLATFORM_OVERRIDE: str | None = None


def current_platform() -> str:
    """Return ``"nt"`` or ``"posix"``, honouring the test override."""
    return PLATFORM_OVERRIDE or os.name


def is_windows() -> bool:
    return current_platform() == "nt"


def safe_child_env() -> dict[str, str]:
    """Build a minimal environment for a worker process.

    Windows environment keys are case-insensitive, so they are matched
    upper-cased; POSIX keys are matched verbatim.
    """
    windows = is_windows()
    return {k: v for k, v in os.environ.items() if (k.upper() if windows else k) in CHILD_ENV_ALLOWLIST}


def worker_process_kwargs() -> dict[str, Any]:
    """Popen kwargs that isolate a child so it can be killed as a unit.

    Windows: a new process group with no console window. POSIX: a new session,
    which gives the child its own process group for ``killpg``.
    """
    if is_windows():
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW}
    return {"start_new_session": True}


def windows_system_dir() -> PureWindowsPath:
    """Absolute path to the Windows system directory.

    Resolved via ``GetSystemDirectoryW`` when actually running on Windows, and
    otherwise hardcoded to the conventional location.

    Deliberately does NOT consult ``SystemRoot``/``WINDIR``: those are ordinary
    environment variables, so trusting them would let anything able to set the
    environment redirect ``taskkill`` to an executable of its choosing. The
    kernel32 call and the literal fallback are both outside that reach.

    Returns a ``PureWindowsPath`` rather than ``Path`` so that joining stays
    backslash-correct even when the Windows branch is exercised from a POSIX
    host (which is exactly what the cross-platform tests do).
    """
    if os.name == "nt":  # real platform check: ctypes.windll only exists there
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf))  # type: ignore[attr-defined]
            if size:
                return PureWindowsPath(buf.value)
        except Exception:
            pass
    return PureWindowsPath(r"C:\Windows\System32")


def taskkill_cmd() -> str:
    """Absolute path to ``taskkill.exe`` (never resolved through ``PATH``)."""
    return str(windows_system_dir() / "taskkill.exe")


def taskkill_env() -> dict[str, str]:
    """Minimal environment for ``taskkill`` itself."""
    root = str(windows_system_dir().parent)
    return {"SystemRoot": root, "WINDIR": root}


def kill_process_group(pid: int) -> None:
    """SIGKILL the process group led by ``pid``. POSIX only.

    ``os.killpg``, ``os.getpgid`` and ``signal.SIGKILL`` simply do not exist on
    Windows, so they are resolved through ``getattr`` rather than referenced
    literally. Two concrete reasons:

    1. A literal ``os.killpg`` fails type checking on Windows — reproduce with
       ``mypy --platform win32``, which is why CI runs that flag.
    2. Tests cannot monkeypatch an attribute the module does not have, so a
       literal reference makes the POSIX branch untestable on Windows.

    Raises ``AttributeError`` where process groups are unavailable; the caller
    already degrades that to ``proc.kill()``.
    """
    getpgid = getattr(os, "getpgid", None)
    killpg = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if getpgid is None or killpg is None or sigkill is None:
        raise AttributeError("process-group signalling is POSIX-only")
    killpg(getpgid(pid), sigkill)


def terminate_process_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and any children it spawned, then close its pipes.

    Best-effort by design: this runs on cleanup paths where raising would mask
    the original error. Every failure mode degrades to ``proc.kill()``.
    """
    if proc.poll() is None:
        if is_windows():
            try:
                subprocess.run(
                    [taskkill_cmd(), "/PID", str(proc.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                    env=taskkill_env(),
                    creationflags=_CREATE_NO_WINDOW,
                    check=False,
                )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                kill_process_group(proc.pid)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe:
                pipe.close()
        except Exception:
            pass
    try:
        proc.wait(timeout=0.5)
    except Exception:
        pass
