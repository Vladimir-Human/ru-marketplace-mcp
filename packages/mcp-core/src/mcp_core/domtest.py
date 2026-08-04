"""Run a connector's real DOM extractor against captured markup.

Four connectors read prices out of a rendered page, and the extractor JS is the
one layer their unit tests never touched: every DNS/Citilink/Lamoda test
monkeypatches the CDP render call and feeds an already-parsed dict. That is
exactly where the July 2026 bugs lived — a page full of products came back with
`title: null` and `price: null`, and 707 tests stayed green.

Closing that hole needs a real DOM. This module executes the connector's actual
extractor source against captured markup in jsdom, so a test can assert on what
the parser produces from bytes the site really served.

Why it lives in mcp-core rather than in one connector's tests: the harness was
written twice before this — once for DNS, once for Citilink, fifty near-identical
lines each. A third source would have made three. Tile resolution and price
selection are already shared through :mod:`mcp_core.dom`; the thing that proves
them belongs in the same place.

**jsdom is an optional developer tool, never a runtime dependency.** Install it
wherever Node resolves from (`npm install jsdom`, or point `NODE_PATH` at an
existing copy) and the DOM assertions run; without it :func:`run_extractor`
raises :class:`JsdomUnavailable` and the calling test is expected to skip. The
pure-Python half of every suite runs either way, so a machine without Node still
gets the price-selection coverage.

Usage::

    from mcp_core.domtest import JsdomUnavailable, run_extractor

    def test_the_grid_parses():
        try:
            payload = run_extractor(server._SEARCH_EXTRACT_JS, FIXTURE, page_url=...)
        except JsdomUnavailable as exc:
            pytest.skip(str(exc))
        assert payload["items"]

One caveat worth knowing before writing assertions: **jsdom does not implement
``innerText``**. That is not a limitation to work around — it is the reason the
shared extractors read ``textContent``. ``innerText`` depends on layout and CSS
visibility, so it differs between a warm tab and a freshly navigated one and
cannot be asserted on reproducibly. An extractor that needs ``innerText`` to work
is an extractor this harness cannot vouch for, by design.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

__all__ = ["JsdomUnavailable", "jsdom_available", "run_extractor"]

# argv: [node, runner.js, <html path>, <extractor path>, <page url>]
_RUNNER_JS = textwrap.dedent(
    """
    const { JSDOM } = require('jsdom');
    const fs = require('fs');
    const html = fs.readFileSync(process.argv[2], 'utf8');
    const src  = fs.readFileSync(process.argv[3], 'utf8');
    const dom = new JSDOM(html, { url: process.argv[4] });
    global.window = dom.window;
    global.document = dom.window.document;
    // The extractor is an arrow function expression; wrap it so eval yields the
    // function rather than parsing it as a block.
    const fn = dom.window.eval('(' + src + ')');
    const out = fn();
    process.stdout.write(typeof out === 'string' ? out : JSON.stringify(out));
    """
)


class JsdomUnavailable(RuntimeError):
    """Node or jsdom is not installed, so the DOM-level check cannot run."""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise JsdomUnavailable("node not available — DOM-level extractor check skipped")
    return node


def _resolve_jsdom() -> str | None:
    """Absolute path of the `node_modules` directory holding jsdom, or None.

    Resolution happens from the working directory — which is where
    `npm install jsdom` puts it — but the runner script lives in a temp
    directory, and Node resolves `require` relative to the *script*, not the
    process. Without passing the location through, a jsdom installed exactly as
    the docs instruct is found by the probe and then not found by the run: the
    check fails instead of skipping, which is worse than either.
    """
    try:
        node = _node()
    except JsdomUnavailable:
        return None
    try:
        probe = subprocess.run(
            [node, "-e", "process.stdout.write(require.resolve('jsdom'))"],
            capture_output=True,
            timeout=30,
            env=dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env dependent
        return None
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    entry = Path(probe.stdout.decode("utf-8").strip())
    for parent in entry.parents:
        if parent.name == "node_modules":
            return str(parent)
    return None


def jsdom_available() -> bool:
    """True when this machine can execute an extractor against real markup."""
    return _resolve_jsdom() is not None


def run_extractor(
    js_source: str,
    html_path: str | Path,
    *,
    page_url: str,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Execute ``js_source`` against ``html_path`` in jsdom and return its output.

    Args:
        js_source: the connector's extractor, verbatim. Pass the module constant
            (``server._SEARCH_EXTRACT_JS``) rather than a copy — a test that
            asserts on a re-typed extractor proves nothing about the shipped one.
        html_path: captured markup to parse.
        page_url: the document URL jsdom should report. Relative ``href``
            attributes resolve against it, so a wrong value silently changes
            every extracted product URL — pass the real search page.
        timeout_s: hard ceiling on the Node process.

    Raises:
        JsdomUnavailable: node or jsdom missing; the caller should skip.
        AssertionError: the extractor itself threw, with Node's stderr attached.
    """
    node = _node()
    modules = _resolve_jsdom()
    if modules is None:
        raise JsdomUnavailable("jsdom not installed (npm install jsdom, or set NODE_PATH) — DOM check skipped")

    env = dict(os.environ)
    # Hand the runner the directory the probe actually found jsdom in.
    existing = env.get("NODE_PATH")
    env["NODE_PATH"] = f"{modules}{os.pathsep}{existing}" if existing else modules
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        extractor = tmp_path / "extract.js"
        extractor.write_text(js_source, encoding="utf-8")
        runner = tmp_path / "runner.js"
        runner.write_text(_RUNNER_JS, encoding="utf-8")
        # Node emits UTF-8; `text=True` would decode it with the locale codec
        # (cp1251 on Russian Windows) and choke on the Cyrillic prices. Capture
        # bytes and decode explicitly so the harness behaves the same everywhere.
        result = subprocess.run(
            [node, str(runner), str(html_path), str(extractor), page_url],
            capture_output=True,
            timeout=timeout_s,
            env=env,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")

    if result.returncode != 0:  # pragma: no cover - surfaces a broken extractor loudly
        raise AssertionError(f"extractor JS failed under jsdom:\n{stderr[:1500]}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed extractor output
        raise AssertionError(f"extractor returned non-JSON output: {stdout[:400]!r}") from exc
