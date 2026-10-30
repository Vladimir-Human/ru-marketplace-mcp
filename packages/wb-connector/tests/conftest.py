"""Marks this directory as its own pytest rootdir package, and keeps offline tests offline.

Several connectors have a ``test_server.py``; without a conftest per directory
pytest cannot tell the identically-named modules apart during collection.

Since ``WB_DOM_FALLBACK`` defaults to on, a faked API refusal in an offline test
would reach ``dom_transport.render`` and drive whatever Chrome listens on the CDP
port — on a developer machine, the operator's real browser loading a real WB
page. The guard below makes ``chrome_cdp.open_page`` (where ``render`` gets its
page) fail at once instead. Tests that exercise the DOM path patch ``open_page``
or ``render`` themselves; ``live`` and ``cdp`` tests are left alone.

The guard raises a ``BaseException`` subclass: the fallback path catches
``Exception`` to name the API refusal, and an ``AssertionError`` turned into a
``transport_down`` let a test pass for the wrong reason (pre-release audit A2).
"""

# START_MODULE_CONTRACT
# PURPOSE: Keep offline WB tests away from the real Chrome now that the DOM fallback is on by default.
# SCOPE: chrome_cdp.open_page is replaced by a guard raising a BaseException subclass; live/cdp tests untouched.
# DEPENDS: pytest; mcp_core.transport.chrome_cdp.
# LINKS: M-WB; R-OFFLINE; R-WB-DOM-FALLBACK.
# END_MODULE_CONTRACT

import pytest
from mcp_core.transport import chrome_cdp


class OfflineBrowserGuard(BaseException):
    """An offline test reached the real Chrome; no ``except Exception`` may hide it."""


@pytest.fixture(autouse=True)
def _offline_tests_never_reach_a_real_browser(request, monkeypatch):
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("cdp"):
        return

    def refuse(*args, **kwargs):
        raise OfflineBrowserGuard("an offline test reached the real Chrome: patch chrome_cdp.open_page or render")

    monkeypatch.setattr(chrome_cdp, "open_page", refuse)
