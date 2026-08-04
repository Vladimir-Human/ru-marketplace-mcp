"""Guard the UTF-8 decoding between Python and Node in the DOM harness.

On a Russian-Windows box the console code page is cp1251. ``subprocess.run``
with ``text=True`` decodes Node's stdout through that codec, and the extractor
output carries Cyrillic product titles — so every jsdom-based DOM test on the
machine that most needs it died with ``UnicodeDecodeError`` while a clean CI
never saw it. Plain bytes captured and decoded as UTF-8 behave identically
everywhere.

This file proves three things about :mod:`mcp_core.domtest`:

* stdout with Cyrillic round-trips through ``run_extractor`` (the regression);
* stderr with Cyrillic reaches an :class:`AssertionError` intact instead of
  raising ``UnicodeDecodeError`` before the message can be built;
* synthetic broken output is refused loudly, not silently coerced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.domtest import JsdomUnavailable, run_extractor

_HERE = Path(__file__).parent
_FIXTURE = _HERE / "fixtures" / "utf8_roundtrip.html"


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(js_source, _FIXTURE, page_url="https://example.test/")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


# The extractors under test produce Cyrillic output through run_extractor.
_SIMPLE_JS = (
    "() => {\n"
    "    const title = document.querySelector('.title');\n"
    "    return JSON.stringify({title: title.textContent, price: 58999.0});\n"
    "}"
)


def test_cyrillic_stdout_survives_the_run() -> None:
    """The regression: cp1251 decoding used to kill jsdom tests on Windows."""
    payload = _extract(_SIMPLE_JS)
    assert payload["title"] == "Ноутбук Леново"
    assert payload["price"] == 58999.0


def test_cyrillic_stderr_reaches_the_assertion_intact() -> None:
    """A throwing extractor must surface its Cyrillic message, not a decode error."""
    js = "() => {\n    throw new Error('экстрактор упал на кириллице');\n}"
    with pytest.raises(AssertionError, match="экстрактор упал на кириллице"):
        _extract(js)


def test_broken_json_is_refused_loudly() -> None:
    """Non-JSON stdout must raise, never pass a silently-decoded blob."""
    js = "() => {\n    return 'not-json-но-с-кириллицей';\n}"
    with pytest.raises(AssertionError):
        _extract(js)
