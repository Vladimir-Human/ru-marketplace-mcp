"""Every registry source is a named parameter for future conformance rules.

START_MODULE_CONTRACT
PURPOSE: Supply one shared source fixture without importing connector servers.
SCOPE: Every mcp_core.sources entry, including profiles; no existing tests moved; no real browser.
DEPENDS: pytest; mcp_core.sources; mcp_core.transport.chrome_cdp.
LINKS: M-ARCH-GATE, M-SOURCES, R-ARCH-GATE.
END_MODULE_CONTRACT
START_MODULE_MAP
  source: Yield the registry entry named by this pytest parameter.
  _no_real_browser: Make every way to a real Chrome fail fast in these offline tests.
END_MODULE_MAP
"""

from collections.abc import Iterator
from typing import Any

import pytest
from mcp_core.sources import SOURCES, SourceSpec
from mcp_core.transport import chrome_cdp

# START_BLOCK_REGISTRY_FIXTURE


@pytest.fixture(params=SOURCES, ids=lambda source: source.name)
def source(request: pytest.FixtureRequest) -> Iterator[SourceSpec]:
    yield request.param


# END_BLOCK_REGISTRY_FIXTURE
# START_BLOCK_NO_REAL_BROWSER


def _blocked(*args: Any, **kwargs: Any) -> Any:
    raise ConnectionError("offline conformance test: a browser call escaped its fake")


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path a test forgot to fake must not start or drive the owner's Chrome.

    Conformance tests run whole tools and selfchecks with fake transports; an
    unfaked companion probe would otherwise reach Playwright or raw CDP and send
    a real request. The guard sits below every ``open_page`` binding (Chrome
    start, Playwright, raw CDP), so a test's own ``open_page`` fake still works.
    """
    monkeypatch.setattr(chrome_cdp, "_start_chrome_with_cdp", lambda **kwargs: (False, "blocked in offline tests"))
    monkeypatch.setattr(chrome_cdp, "async_playwright", _blocked)
    monkeypatch.setattr(chrome_cdp, "_cdp_http_json", _blocked)
    monkeypatch.setattr(chrome_cdp, "_websockets", None)


# END_BLOCK_NO_REAL_BROWSER
