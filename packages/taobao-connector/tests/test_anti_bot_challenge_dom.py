"""The real search extractor against challenge-widget pages — DOM level.

The live probe that earned this file (2026-09-10): healthy logged-out search
pages rendering 29-38 items answered ``title='__BLOCKED__'`` because the JS
captcha verdict scanned ``document.body.textContent`` — INCLUDING the hidden
baxia challenge widget's text «人机», which rides in the DOM even when no
challenge is served. ``taobao_selfcheck`` reported inconclusive(blocked) and
the shape-drift canary never ran. The verdict moved to Python
(``_anti_bot_challenge``), gated on ZERO extracted items.

These tests build both page shapes offline: the committed hand-modeled grid
(``fixtures/search_grid.html``) with an injected hidden widget div, and a pure
challenge page (zero items, visible widget text). They run the REAL extractor
under jsdom (skips without it) and assert both halves of the fix: the JS bakes
no verdict — ``title`` stays the raw document title — and the Python verdict
reads the page with items as a catalog and the zero-item widget page as a
challenge.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

GRID = Path(__file__).parent / "fixtures" / "search_grid.html"
CARD = Path(__file__).parent / "fixtures" / "item_card.html"
PAGE_URL = "https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA"
CARD_URL = "https://item.taobao.com/item.htm?id=123456789012"

# The hidden widget healthy pages carry in the DOM: display:none, but its text
# still rides document.body.textContent — exactly what convicted them under the
# old JS verdict. cleanTextWithout drops script/style only, so the text lands
# in body_snippet too; the zero-items gate is what must absorb it.
HIDDEN_BAXIA = """
<div id="baxia-dialog" style="display:none" aria-hidden="true">
    人机验证 — 亲，请完成智能验证后继续访问
</div>
"""

CHALLENGE_HTML = """<!doctype html>
<html lang="zh-CN">
    <head>
        <meta charset="utf-8" />
        <title>淘宝网</title>
    </head>
    <body>
        <div id="baxia-punish">
            <p>亲，请拖动下方滑块完成验证后继续浏览</p>
            <p>Sliding captcha — are you human?</p>
        </div>
    </body>
</html>
"""


def _extract(html_path: Path) -> dict:
    try:
        return run_extractor(server._SEARCH_EXTRACT_JS, html_path, page_url=PAGE_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def _grid_with_hidden_widget(tmp_path: Path) -> Path:
    """The committed modeled grid with a hidden baxia widget injected."""
    html = GRID.read_text(encoding="utf-8")
    page = tmp_path / "grid_hidden_baxia.html"
    page.write_text(html.replace("</body>", f"{HIDDEN_BAXIA}</body>"), encoding="utf-8")
    return page


def test_a_healthy_grid_with_a_hidden_challenge_widget_is_not_blocked(tmp_path) -> None:
    """The 2026-09-10 regression: the grid renders its two items AND the
    hidden widget's text (人机/验证) rides body.textContent. The old JS verdict
    baked title='__BLOCKED__'; the shipped extractor must return the raw
    document title, and the Python verdict must read the page as a catalog."""
    payload = _extract(_grid_with_hidden_widget(tmp_path))

    # Transport: no verdict baked in — raw document title, items intact.
    assert payload["title"] == "手机-淘宝搜索"
    assert len(payload["items"]) == 2
    # The hidden widget's text DOES reach the visibility-filtered snippet
    # (display:none is not filtered) — the items gate, not the text filter,
    # is what saves this page.
    assert "人机" in payload["body_snippet"]

    # Verdicts: a catalog, not a challenge — and not a login wall either.
    assert server._anti_bot_challenge(payload) is False
    assert server._login_wall_markers(payload) == ()


def test_a_genuine_challenge_page_reads_as_blocked(tmp_path) -> None:
    """The other half: a real challenge — zero items, the catalog replaced by
    the widget — must still produce the blocked verdict, now from Python. The
    JS emits raw fields only."""
    page = tmp_path / "challenge.html"
    page.write_text(CHALLENGE_HTML, encoding="utf-8")

    payload = _extract(page)

    assert payload["items"] == []
    assert payload["title"] == "淘宝网", "raw document title — the JS bakes no verdict"
    assert server._anti_bot_challenge(payload) is True
    assert server._login_wall_markers(payload) == (), "a captcha is not a login wall"


def test_script_text_cannot_fake_a_challenge(tmp_path) -> None:
    """The verdict reads the visibility-filtered snippet: a page whose only
    challenge wording lives inside inline JS is not a challenge. The old
    body.textContent scan included script text and would have convicted it;
    cleanTextWithout drops script/style before the snippet is built."""
    html = GRID.read_text(encoding="utf-8")
    script = '<script>var baxiaConfig = {"text": "验证码 人机 captcha are you human"};</script>'
    page = tmp_path / "grid_script_only.html"
    page.write_text(html.replace("</body>", f"{script}</body>"), encoding="utf-8")

    payload = _extract(page)

    assert len(payload["items"]) == 2
    for word in ("验证码", "captcha", "are you human"):
        assert word not in payload["body_snippet"], "script text leaked into the visible snippet"
    assert server._anti_bot_challenge(payload) is False


def _extract_card(html_path: Path) -> dict:
    try:
        return run_extractor(server._CARD_EXTRACT_JS, html_path, page_url=CARD_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_card_extractor_generic_title_challenge_is_recognized_by_handoff(tmp_path) -> None:
    page = tmp_path / "card_challenge.html"
    page.write_text(CHALLENGE_HTML, encoding="utf-8")
    payload = _extract_card(page)

    assert payload["title"] == "淘宝网", "the document-title fallback is not product evidence"
    assert payload["page_title"] == "淘宝网"
    assert server._anti_bot_challenge(payload) is True
    assert server._page_challenge_kind(payload) == "captcha"


@pytest.mark.parametrize(
    "hidden_widget",
    [
        HIDDEN_BAXIA,
        "<div hidden><p>Please complete CAPTCHA</p></div>",
        '<div aria-hidden="true"><p>Please complete CAPTCHA</p></div>',
        '<style>.passive {display: none}</style><div class="passive"><p>Please complete CAPTCHA</p></div>',
        '<div style="visibility:hidden"><p>Please complete CAPTCHA</p></div>',
    ],
    ids=["baxia", "hidden_attribute", "aria_hidden", "stylesheet", "visibility_hidden"],
)
def test_card_extractor_hidden_widget_cannot_fake_a_challenge(tmp_path, hidden_widget) -> None:
    html = CARD.read_text(encoding="utf-8")
    page = tmp_path / "card_hidden_widget.html"
    page.write_text(html.replace("</body>", f"{hidden_widget}</body>"), encoding="utf-8")
    payload = _extract_card(page)

    assert "验证" in payload["body_snippet"] or "CAPTCHA" in payload["body_snippet"]
    assert server._anti_bot_challenge(payload) is False
    assert server._page_challenge_kind(payload) is None


def test_card_extractor_visible_challenge_overrides_product_fields(tmp_path) -> None:
    html = CARD.read_text(encoding="utf-8")
    page = tmp_path / "card_visible_challenge.html"
    widget = '<div id="baxia-punish">Please complete CAPTCHA</div>'
    page.write_text(html.replace("</body>", f"{widget}</body>"), encoding="utf-8")
    payload = _extract_card(page)

    assert payload["title"].startswith("Apple iPhone")
    assert payload["price_texts"]["attached"]
    assert server._anti_bot_challenge(payload) is True
    assert server._page_challenge_kind(payload) == "captcha"
