#!/usr/bin/env python3
"""Count every outgoing marketplace request against the owner's live-run budget.

The budget is 40 requests per run and 8 per marketplace, with at least 3 s
between two requests to the same marketplace. It was breached once because
Ozon's Tier-1 curl runs in a child process, out of reach of an in-process HTTP
hook; that is why this module hooks the parent's hand-off point as well as the
transports, and why ``install()`` refuses to continue when a hook point has
moved instead of silently counting nothing.

Limits come from ``LIVE_BUDGET_TOTAL``, ``LIVE_BUDGET_PER_SOURCE`` and
``LIVE_BUDGET_GAP_S``. The ledger lives in memory, or in the JSON file named by
``LIVE_BUDGET_LEDGER`` (atomic replace), so several ``live_check.py`` commands
share one run. Two processes share one file too (``pytest -m live`` next to
``live_check.py``), so the file is read and written under a lock file next to
it. Loopback requests (the local Chrome CDP endpoint) are not counted.

A request over either cap raises ``BudgetExhausted`` before it is sent. A
request that comes too soon after the previous one to the same marketplace
sleeps out the rest of the gap first. Every counted request prints one line to
stderr: ``[req n/40] ozon 3/8 httpx https://...``.

START_MODULE_CONTRACT
PURPOSE: Enforce the owner's live-run request budget across every transport a connector uses.
SCOPE: Env limits, grouped ledger (memory/file, atomic, cross-process lock), reservation/gap, transport hooks, install/uninstall.
DEPENDS: stdlib asyncio,contextlib,datetime,ipaddress,json,os,re,sys,tempfile,threading,time,urllib.parse; httpx, curl_cffi, Playwright, mcp_core.transport.chrome_cdp, ozon_connector.server at install time.
LINKS: M-LIVE-BUDGET, R-LIVE-BUDGET, R-TRANSPORT.
START_MODULE_MAP
  BudgetExhausted      raised before a request over the total or per-source cap
  Ledger               in-memory or JSON-file request ledger
  Budget               limits, reservation, gap waits and stderr lines
  source_group         marketplace group for a host
  is_loopback          local CDP hosts that are not counted
  _window_open_url     the URL a window.open() evaluation would load
  _ledger_lock         the cross-process lock around the ledger file
  install              wrap every hook point, loudly on a missing one
  uninstall            restore the wrapped methods
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_TOTAL = 40
DEFAULT_PER_SOURCE = 8
DEFAULT_GAP_S = 3.0

# The cross-process lock around the ledger file: how often to retry, how long
# to wait before giving up, and when a lock file counts as abandoned.
LOCK_RETRY_S = 0.05
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 60.0

# Marketplace host groups. A request is charged to the group whose suffix it
# matches, so card and content CDNs count against the same marketplace.
GROUPS: dict[str, tuple[str, ...]] = {
    "wildberries": ("wb.ru", "wildberries.ru", "wbbasket.ru", "wbcontent.net", "wbstatic.net"),
    "ozon": ("ozon.ru", "ozone.ru", "ozon.com"),
    "yandex": ("yandex.ru", "ya.ru", "yandex.net"),
    "detmir": ("detmir.ru",),
    "avito": ("avito.ru", "avito.st"),
    "taobao": ("taobao.com", "tmall.com"),
    "megamarket": ("megamarket.ru", "sbermegamarket.ru"),
    "lamoda": ("lamoda.ru",),
    "dns": ("dns-shop.ru",),
    "citilink": ("citilink.ru",),
    "aliexpress": ("aliexpress.ru", "aliexpress.com"),
    "cian": ("cian.ru",),
    "mpstats": ("mpstats.io",),
}

_MISSING = object()


class BudgetExhausted(RuntimeError):
    """The next request would exceed the total or per-marketplace cap."""


def is_loopback(host: str | None) -> bool:
    """True for the local CDP endpoint and other loopback hosts."""
    if host is None or not host:
        return True
    name = host.strip("[]").lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def source_group(host: str) -> str:
    """The marketplace group a host belongs to, or its registrable-ish suffix."""
    name = host.lower().rstrip(".")
    for group, suffixes in GROUPS.items():
        if any(name == suffix or name.endswith("." + suffix) for suffix in suffixes):
            return group
    labels = name.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else name


def _env_number(name: str, default: int | float, cast: Callable[[str], int | float]) -> int | float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {raw!r}")
    return value


class Ledger:
    """Request entries shared by every budget install in one live run."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._entries: list[dict[str, Any]] = []

    def load(self) -> list[dict[str, Any]]:
        if self.path is None:
            return list(self._entries)
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError(f"{self.path} is not a live-budget ledger: no entries list")
        return entries

    def save(self, entries: list[dict[str, Any]]) -> None:
        """Replace the ledger atomically so a crashed run cannot leave half a file."""
        if self.path is None:
            self._entries = list(entries)
            return
        handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"entries": entries}, stream, ensure_ascii=False, indent=1)
                stream.write("\n")
            os.replace(temporary, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise


def _lock_is_stale(lock_path: Path) -> bool:
    """True when a lock file is older than a minute: its owner is gone."""
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    return age > LOCK_STALE_S


@contextlib.contextmanager
def _ledger_lock(path: Path | None) -> Iterator[None]:
    """Hold the cross-process lock on the ledger while it is read and written.

    The ledger is a read-modify-write: two processes that read it at the same
    time both append and the second save drops the first one's bookings. The
    lock file is created exclusively next to the ledger, so only one process
    can be inside the critical section; the holder releases it in ``finally``,
    and a lock older than a minute is treated as abandoned and removed.

    ``None`` means the ledger lives in memory, where no other process can see
    it and no lock is needed.
    """
    if path is None:
        yield
        return
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as busy:
            if _lock_is_stale(lock_path):
                with contextlib.suppress(OSError):
                    os.unlink(lock_path)
                continue
            if time.monotonic() >= deadline:
                raise BudgetExhausted(
                    f"live budget ledger is locked by another run: {lock_path} "
                    f"(waited {int(LOCK_TIMEOUT_S)} s; the lock is removed after {int(LOCK_STALE_S)} s)"
                ) from busy
            time.sleep(LOCK_RETRY_S)
            continue
        break
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock_path)


class Budget:
    """The caps, the ledger and the gaps between requests."""

    def __init__(
        self,
        total: int,
        per_source: int,
        gap_s: float,
        ledger: Ledger,
        *,
        clock: Callable[[], float] = time.time,
        sleep_sync: Callable[[float], None] = time.sleep,
        sleep_async: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.total = total
        self.per_source = per_source
        self.gap_s = gap_s
        self.ledger = ledger
        self._clock = clock
        self._sleep_sync = sleep_sync
        self._sleep_async = sleep_async
        self._lock = threading.Lock()
        self.refused: list[str] = []

    @classmethod
    def from_env(cls, ledger_path: Path | None = None) -> Budget:
        """Build a budget from the documented environment variables."""
        total = int(_env_number("LIVE_BUDGET_TOTAL", DEFAULT_TOTAL, int))
        per_source = int(_env_number("LIVE_BUDGET_PER_SOURCE", DEFAULT_PER_SOURCE, int))
        gap_s = float(_env_number("LIVE_BUDGET_GAP_S", DEFAULT_GAP_S, float))
        if ledger_path is None:
            configured = os.environ.get("LIVE_BUDGET_LEDGER")
            ledger_path = Path(configured) if configured else None
        return cls(total, per_source, gap_s, Ledger(ledger_path))

    def reserve(self, kind: str, url: str) -> float:
        """Book one request and return how long the caller must wait first.

        Raises ``BudgetExhausted`` before booking when the request is over a
        cap. The booking time already includes a concurrent caller's gap, so
        two coroutines cannot both fire at the previous request's timestamp.
        The whole read-check-append-save runs under the ledger lock, so a
        second process cannot lose this booking by saving over it.
        """
        host = urlsplit(url).hostname
        if is_loopback(host):
            return 0.0
        group = source_group(host or "")
        refusal = ""
        now = at = 0.0
        number = count = 0
        with self._lock, _ledger_lock(self.ledger.path):
            entries = self.ledger.load()
            mine = [entry for entry in entries if entry.get("group") == group]
            if len(entries) >= self.total:
                refusal = f"live budget exhausted: {len(entries)}/{self.total} total (a new run starts after `python scripts/live_check.py reset`)"
            elif len(mine) >= self.per_source:
                refusal = f"live budget exhausted for {group}: {len(mine)}/{self.per_source} requests (a new run starts after `python scripts/live_check.py reset`)"
            else:
                now = self._clock()
                last = max((float(entry.get("t", 0.0)) for entry in mine), default=0.0)
                at = max(now, last + self.gap_s)
                entries.append(
                    {
                        "t": at,
                        "utc": dt.datetime.fromtimestamp(at, dt.UTC).strftime("%Y-%m-%d %H:%M:%S"),
                        "group": group,
                        "kind": kind,
                        "url": url[:160],
                    }
                )
                self.ledger.save(entries)
                number = len(entries)
                count = len(mine) + 1
        if refusal:
            # A connector maps the refusal to its own transport error, so a
            # selfcheck row reads transport_down; this line and the ledger
            # summary are where the operator sees that the budget said no.
            self.refused.append(group)
            print(f"[refused] {group} {kind} {url[:160]}: {refusal}", file=sys.stderr)
            sys.stderr.flush()
            raise BudgetExhausted(refusal)
        print(f"[req {number}/{self.total}] {group} {count}/{self.per_source} {kind} {url[:160]}", file=sys.stderr)
        sys.stderr.flush()
        return at - now

    def reserve_sync(self, kind: str, url: str) -> None:
        wait = self.reserve(kind, url)
        if wait > 0:
            self._sleep_sync(wait)

    async def reserve_async(self, kind: str, url: str) -> None:
        wait = self.reserve(kind, url)
        if wait > 0:
            await self._sleep_async(wait)


class _Installation:
    def __init__(self, budget: Budget, restores: list[tuple[Any, str, Any, Any]]) -> None:
        self.budget = budget
        self.restores = restores


_installation: _Installation | None = None


def _hook(owner: Any, attribute: str, label: str) -> Any:
    """Read one hook point, failing loudly instead of silently under-counting."""
    original = getattr(owner, attribute, _MISSING)
    if original is _MISSING:
        raise AttributeError(
            f"live budget hook point {label} is missing; the code was renamed or removed, "
            "so requests would no longer be counted"
        )
    return original


def _replace(
    owner: Any,
    attribute: str,
    wrapper: Any,
    restores: list[tuple[Any, str, Any, Any]],
    label: str,
) -> None:
    original = _hook(owner, attribute, label)
    setattr(owner, attribute, wrapper)
    restores.append((owner, attribute, original, wrapper))


def _restore(restores: list[tuple[Any, str, Any, Any]]) -> None:
    """Put the originals back, unless something else replaced our wrapper since."""
    for owner, attribute, original, wrapper in reversed(restores):
        if getattr(owner, attribute, None) is wrapper:
            setattr(owner, attribute, original)


def _url_text(url: Any) -> str:
    return url if isinstance(url, str) else str(url)


def _fetch_url(expression: Any, arg: Any) -> str | None:
    """The URL of an in-page fetch evaluation, when the call carries one."""
    if "fetch(" not in str(expression):
        return None
    if isinstance(arg, dict) and isinstance(arg.get("url"), str):
        return arg["url"]
    return None


# The URL a `window.open(...)` call would load: the first argument, when it is
# a JSON string literal. Anything else (a variable, a template, a number) is
# not a URL this hook can read.
_WINDOW_OPEN = re.compile(r"window\.open\(\s*(\"(?:[^\"\\]|\\.)*\")")


def _window_open_url(expression: Any) -> str | None:
    """The URL a ``window.open(...)`` evaluation would load, when it names one.

    The opener AliExpress builds is ``window.open(<json url>, '_blank')``, so
    the URL is read as a JSON string literal instead of by hand. A URL that is
    not http(s) is ignored: it is not a marketplace request.
    """
    match = _WINDOW_OPEN.search(str(expression))
    if match is None:
        return None
    try:
        url = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return None
    return url


def _install_httpx(budget: Budget, restores: list[tuple[Any, str, Any, Any]]) -> None:
    import httpx

    async_send = _hook(httpx.AsyncClient, "send", "httpx.AsyncClient.send")
    sync_send = _hook(httpx.Client, "send", "httpx.Client.send")

    async def counted_async_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        await budget.reserve_async("httpx", str(request.url))
        return await async_send(self, request, *args, **kwargs)

    def counted_sync_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        budget.reserve_sync("httpx", str(request.url))
        return sync_send(self, request, *args, **kwargs)

    _replace(httpx.AsyncClient, "send", counted_async_send, restores, "httpx.AsyncClient.send")
    _replace(httpx.Client, "send", counted_sync_send, restores, "httpx.Client.send")


def _install_curl_cffi(budget: Budget, restores: list[tuple[Any, str, Any, Any]]) -> None:
    from curl_cffi import requests as curl_requests

    sync_request = _hook(curl_requests.Session, "request", "curl_cffi.requests.Session.request")
    async_request = _hook(curl_requests.AsyncSession, "request", "curl_cffi.requests.AsyncSession.request")

    def counted_sync_request(self: Any, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        budget.reserve_sync("curl_cffi", _url_text(url))
        return sync_request(self, method, url, *args, **kwargs)

    async def counted_async_request(self: Any, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        await budget.reserve_async("curl_cffi", _url_text(url))
        return await async_request(self, method, url, *args, **kwargs)

    _replace(curl_requests.Session, "request", counted_sync_request, restores, "curl_cffi.requests.Session.request")
    _replace(
        curl_requests.AsyncSession,
        "request",
        counted_async_request,
        restores,
        "curl_cffi.requests.AsyncSession.request",
    )


def _install_chrome_cdp(budget: Budget, restores: list[tuple[Any, str, Any, Any]]) -> None:
    from mcp_core.transport import chrome_cdp
    from playwright.async_api import Page

    playwright_page = _hook(chrome_cdp, "_playwright_page", "mcp_core.transport.chrome_cdp._playwright_page")
    raw_cdp_page = _hook(chrome_cdp, "_raw_cdp_page", "mcp_core.transport.chrome_cdp._raw_cdp_page")
    raw_evaluate = _hook(
        chrome_cdp._RawCdpPage,
        "evaluate",
        "mcp_core.transport.chrome_cdp._RawCdpPage.evaluate",
    )
    page_evaluate = _hook(Page, "evaluate", "playwright.async_api.Page.evaluate")
    open_tabs = _hook(chrome_cdp, "open_tabs", "mcp_core.transport.chrome_cdp.open_tabs")

    @contextlib.asynccontextmanager
    async def counted_playwright_page(url: str, *args: Any, **kwargs: Any) -> Any:
        await budget.reserve_async("chrome-load", url)
        async with playwright_page(url, *args, **kwargs) as page:
            yield page

    @contextlib.asynccontextmanager
    async def counted_raw_cdp_page(url: str, *args: Any, **kwargs: Any) -> Any:
        await budget.reserve_async("chrome-load(raw)", url)
        async with raw_cdp_page(url, *args, **kwargs) as page:
            yield page

    async def book_evaluation(expression: Any, arg: Any) -> None:
        """Book every marketplace request an in-page evaluation would send."""
        url = _fetch_url(expression, arg)
        if url:
            await budget.reserve_async("in-page fetch", url)
        opened = _window_open_url(expression)
        if opened:
            await budget.reserve_async("in-page open", opened)

    async def counted_raw_evaluate(self: Any, expression: Any, arg: Any = None, *args: Any, **kwargs: Any) -> Any:
        await book_evaluation(expression, arg)
        return await raw_evaluate(self, expression, arg, *args, **kwargs)

    async def counted_page_evaluate(self: Any, expression: Any, arg: Any = None, *args: Any, **kwargs: Any) -> Any:
        await book_evaluation(expression, arg)
        return await page_evaluate(self, expression, arg, *args, **kwargs)

    async def counted_open_tabs(urls: list[str]) -> Any:
        # Every URL is booked before the first tab is opened: a run that would
        # pass a cap must not open one tab of it.
        for url in urls:
            await budget.reserve_async("chrome-tab", url)
        return await open_tabs(urls)

    _replace(chrome_cdp, "_playwright_page", counted_playwright_page, restores, "chrome_cdp._playwright_page")
    _replace(chrome_cdp, "_raw_cdp_page", counted_raw_cdp_page, restores, "chrome_cdp._raw_cdp_page")
    _replace(chrome_cdp._RawCdpPage, "evaluate", counted_raw_evaluate, restores, "chrome_cdp._RawCdpPage.evaluate")
    _replace(Page, "evaluate", counted_page_evaluate, restores, "playwright.async_api.Page.evaluate")
    _replace(chrome_cdp, "open_tabs", counted_open_tabs, restores, "chrome_cdp.open_tabs")


def _install_ozon(budget: Budget, restores: list[tuple[Any, str, Any, Any]]) -> None:
    import ozon_connector.server as ozon_server

    bounded = _hook(ozon_server, "_run_sync_bounded", "ozon_connector.server._run_sync_bounded")

    async def counted_bounded(func: Any, *args: Any, **kwargs: Any) -> Any:
        # Ozon's Tier-1 curl runs in a child process, out of reach of the
        # transport hooks; count it where the parent hands over the URL.
        url = args[0] if args and isinstance(args[0], str) and args[0].startswith(("http://", "https://")) else None
        if url:
            await budget.reserve_async("curl(subprocess)", url)
        return await bounded(func, *args, **kwargs)

    _replace(ozon_server, "_run_sync_bounded", counted_bounded, restores, "ozon_connector.server._run_sync_bounded")


def install(budget: Budget | None = None) -> Budget:
    """Install the budget hooks once; a second call is a no-op.

    Every hook point is validated before any method is replaced, and a failure
    during installation restores what was already replaced. A renamed hook
    point therefore stops the run loudly instead of quietly dropping requests
    from the count.
    """
    global _installation
    if _installation is not None:
        if budget is not None and budget is not _installation.budget:
            raise RuntimeError("the live budget is already installed with a different budget")
        return _installation.budget

    active = budget if budget is not None else Budget.from_env()
    restores: list[tuple[Any, str, Any, Any]] = []
    try:
        _install_httpx(active, restores)
        _install_curl_cffi(active, restores)
        _install_chrome_cdp(active, restores)
        _install_ozon(active, restores)
    except BaseException:
        _restore(restores)
        raise
    _installation = _Installation(active, restores)
    return active


def uninstall() -> None:
    """Restore every wrapped method; used by tests and by explicit teardown."""
    global _installation
    if _installation is None:
        return
    _restore(_installation.restores)
    _installation = None


def installed_budget() -> Budget | None:
    """The active budget, if install() has run in this process."""
    return _installation.budget if _installation is not None else None
