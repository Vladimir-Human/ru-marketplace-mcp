"""Authenticated transport: drive the operator's own Chrome over CDP.

Several Russian marketplaces reject datacenter TLS fingerprints outright — Ozon
answers ``composer-api.bx`` with an endless 307 ``__rr`` redirect loop, and
others gate catalog reads behind a session cookie. The reliable answer is not a
better fingerprint: it is to run the fetch *inside* a real browser the operator
already trusts. This module connects to a Chrome started with
``--remote-debugging-port`` and reuses that live session.

=== THREAT MODEL — READ BEFORE USING ===

CDP hands any local caller full control of the profile it is attached to,
including every logged-in session in it. The mitigations, in order of value:

1. **Dedicated profile.** ``CHROME_SCRAPING_PROFILE`` defaults to a scraping-only
   directory, never the operator's daily profile. Log into marketplaces there
   and nothing else — banking and email stay out of blast radius.
2. **Localhost binding.** Chrome is launched with
   ``--remote-debugging-address=127.0.0.1``; the port is never exposed to a LAN.
3. **Scheme guard.** ``open_page`` refuses anything but http(s), so an
   authenticated browser can never be aimed at ``file:///``.
4. **Caller-side host allowlists.** Each connector validates its own URLs before
   they reach this module — that is what stops a crafted SKU from turning into a
   request for ``/api/personal/orders``.

No credentials are ever stored, read, or transmitted by this code: the operator
logs in by hand, in a browser they control.

Cross-platform: Chrome binaries and profile locations are resolved per platform
(Windows / macOS / Linux). Window-hiding stealth is Windows-only; elsewhere the
browser launches normally, or headless when ``CHROME_HEADLESS=1``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


def _port_from_env() -> int:
    raw = os.environ.get("CHROME_CDP_PORT", "9222")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 9222
    return port if 1 <= port <= 65535 else 9222


CDP_PORT = _port_from_env()
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"


def _default_profile_dir() -> Path:
    """Platform-appropriate location for the dedicated scraping profile."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "Chrome-Scraping"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chrome-Scraping"
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "chrome-scraping"


SCRAPING_PROFILE = os.environ.get("CHROME_SCRAPING_PROFILE", str(_default_profile_dir()))

# Windows-only: park the scraping window off-screen so it never steals focus.
# A real (non-headless) window keeps Chrome's renderer fingerprint intact, which
# is the whole point of using CDP instead of a headless scraper.
STEALTH = os.environ.get("CHROME_STEALTH", "1") != "0"

# Opt-in headless mode for Linux hosts with no display. Anti-bot systems detect
# headless Chrome readily, so this stays off by default.
HEADLESS = os.environ.get("CHROME_HEADLESS", "0") == "1"


def _chrome_candidates() -> list[str]:
    """Chrome/Chromium executables to try, most preferred first."""
    override = os.environ.get("CHROME_BINARY")
    found: list[str] = [override] if override else []

    if sys.platform == "win32":
        # Windows env keys are case-insensitive, and "PROGRAMFILES(X86)" does not
        # exist in the upper-cased form ruff's SIM112 suggests — keep the real names.
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")  # noqa: SIM112
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")  # noqa: SIM112
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        found += [
            rf"{program_files}\Google\Chrome\Application\chrome.exe",
            rf"{program_files_x86}\Google\Chrome\Application\chrome.exe",
            rf"{program_files}\Microsoft\Edge\Application\msedge.exe",
        ]
        if local_app_data:
            found.append(rf"{local_app_data}\Google\Chrome\Application\chrome.exe")
    elif sys.platform == "darwin":
        found += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            resolved = shutil.which(name)
            if resolved:
                found.append(resolved)
        found += ["/usr/bin/google-chrome", "/usr/bin/chromium", "/snap/bin/chromium"]
    return [p for p in found if p]


_AUTOSTART_LOCK = asyncio.Lock()


def _cdp_port_open() -> bool:
    """Quick TCP probe: is something already listening on the CDP port?"""
    try:
        with socket.create_connection(("127.0.0.1", CDP_PORT), timeout=0.5):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def _find_chrome() -> str | None:
    for candidate in _chrome_candidates():
        if Path(candidate).exists():
            return candidate
    return None


def _running_as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def _start_chrome_with_cdp() -> tuple[bool, str]:
    """Spawn Chrome with remote debugging enabled. Returns ``(ok, detail)``.

    Idempotent by contract: callers check ``_cdp_port_open()`` first. Chrome
    enforces ``--user-data-dir`` exclusivity, so a lost startup race simply
    means the second process exits on its own.
    """
    chrome = _find_chrome()
    if not chrome:
        return False, "Chrome/Chromium not found — set CHROME_BINARY to its full path"

    try:
        profile_dir = Path(SCRAPING_PROFILE)
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"unusable scraping profile {SCRAPING_PROFILE!r}: {exc}"

    args = [
        chrome,
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if HEADLESS:
        args += ["--headless=new", "--disable-gpu"]
    elif STEALTH and sys.platform == "win32":
        args += ["--window-position=-32000,-32000", "--window-size=1280,720", "--start-minimized"]

    if sys.platform.startswith("linux") and _running_as_root():
        # Chrome refuses to run its sandbox as root, and containers routinely are.
        args.append("--no-sandbox")

    # Anchor to about:blank so Chrome survives the connector closing its last
    # tab — otherwise every call pays the full spawn + CDP-bind wait again.
    args.append("about:blank")

    try:
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlive this server.
            subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True)
        else:
            subprocess.Popen(
                args,
                start_new_session=True,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        return False, f"failed to spawn chrome: {exc}"
    return True, f"started chrome with profile {profile_dir}"


async def _ensure_cdp_running(timeout_s: float = 12.0) -> tuple[bool, str]:
    """Make sure Chrome is listening on the CDP port, auto-starting if needed."""
    async with _AUTOSTART_LOCK:
        if _cdp_port_open():
            return True, "already running"

        ok, msg = await asyncio.to_thread(_start_chrome_with_cdp)
        if not ok:
            return False, msg

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.4)
            if _cdp_port_open():
                await asyncio.sleep(0.6)  # grace: the page launcher lags the socket
                if STEALTH and sys.platform == "win32":
                    await asyncio.to_thread(_hide_chrome_windows)
                return True, msg
        return False, f"chrome started but CDP did not bind within {timeout_s}s"


def _scraping_profile_pids() -> set[int]:
    """PIDs of Chrome processes bound to *our* scraping profile (Windows only).

    Any failure returns an empty set, which makes the caller hide nothing —
    leaving a scraping window visible beats hiding the operator's real browser.
    """
    if sys.platform != "win32":
        return set()
    profile_marker = str(Path(SCRAPING_PROFILE))
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
            "| Where-Object { $_.CommandLine -match [regex]::Escape('"
            + profile_marker.replace("'", "''")
            + "') } | Select-Object -ExpandProperty ProcessId"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0:
            return set()
        return {int(line.strip()) for line in proc.stdout.splitlines() if line.strip().isdigit()}
    except Exception:
        return set()


def _hide_chrome_windows() -> None:
    """Hide scraping-profile Chrome windows (Windows only, best-effort).

    Only windows whose PID is confirmed to belong to the scraping profile are
    touched; ambiguity means do nothing.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        target_pids = _scraping_profile_pids()
        if not target_pids:
            return

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        sw_hide = 0
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):  # pragma: no cover - Windows-only path
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in target_pids:
                user32.ShowWindow(hwnd, sw_hide)
            return True

        user32.EnumWindows(enum_proc(callback), 0)
    except Exception:
        return


def cdp_setup_hint() -> str:
    """Platform-appropriate one-liner for getting CDP running."""
    if sys.platform == "win32":
        return "run scripts/start_chrome_cdp.ps1 (PowerShell)"
    return "run scripts/start_chrome_cdp.sh"


@asynccontextmanager
async def get_browser() -> AsyncIterator[Browser]:
    """Connect to the operator's Chrome over CDP, auto-starting it if needed."""
    if not _cdp_port_open():
        ok, _msg = await _ensure_cdp_running()
        if not ok:
            # The raw detail can contain an absolute profile path (and thus the
            # OS username), so it never reaches a tool-visible error string.
            raise RuntimeError(
                f"CDP autostart failed (Chrome not reachable on 127.0.0.1:{CDP_PORT} — {cdp_setup_hint()})"
            )

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        try:
            yield browser
        finally:
            # Detach, never close: this is the operator's browser. Bounded so a
            # wedged transport cannot hang teardown.
            try:
                await asyncio.wait_for(browser.close(), timeout=10.0)
            except Exception:
                pass


@asynccontextmanager
async def get_context() -> AsyncIterator[BrowserContext]:
    """Yield the profile's default context, cookies and all."""
    async with get_browser() as browser:
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError(f"No browser contexts on CDP {CDP_URL}. Is Chrome running with --remote-debugging-port?")
        yield contexts[0]


# Main-document statuses that mean the navigation failed. Playwright resolves
# goto() for these instead of raising, so we must refuse to hand a block page or
# login wall to a parser as though it were data.
_NAV_FAIL_STATUSES = frozenset({401, 403, 407, 429, 500, 502, 503, 504})
_TAB_OP_TIMEOUT_S = 25.0


class NavBlocked(RuntimeError):
    """A block/auth/error status came back for the main document.

    Carries status and final URL so connectors can surface a clean
    ``transport_down`` / ``rate_limited`` error instead of parsing a login wall.
    """

    def __init__(self, status: int | None, final_url: str = "") -> None:
        self.status = status
        self.final_url = final_url
        super().__init__(f"navigation blocked: HTTP {status}")


@asynccontextmanager
async def open_page(url: str, wait_ms: int = 5000) -> AsyncIterator[Page]:
    """Open a tab on ``url`` in the operator's Chrome, yield it, then close it.

    Guarantees:
      * non-http(s) schemes are refused outright (scheme guard);
      * a block/auth/5xx main document raises ``NavBlocked`` rather than
        yielding a page that only looks like content;
      * tab creation, navigation and teardown are individually bounded, so a
        wedged CDP session cannot hang a tool call indefinitely.

    Host allowlisting stays the caller's responsibility — each connector knows
    which paths are legitimate for its marketplace.
    """
    low = (url or "").strip().lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError("open_page refuses a non-http(s) URL (scheme guard)")

    async with get_context() as ctx:
        page = await asyncio.wait_for(ctx.new_page(), timeout=_TAB_OP_TIMEOUT_S)
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            status = resp.status if resp is not None else None
            if status in _NAV_FAIL_STATUSES:
                final = ""
                try:
                    final = page.url
                except Exception:
                    pass
                raise NavBlocked(status, final)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            if STEALTH and sys.platform == "win32":
                await asyncio.to_thread(_hide_chrome_windows)
            yield page
        finally:
            try:
                await asyncio.wait_for(page.close(), timeout=_TAB_OP_TIMEOUT_S)
            except Exception:
                pass
