"""WB's own answers must not cut diagnostics silently (R18).

The DOM path used to hand the agent ``warnings[:10]``: twelve diagnostics
became ten with no word that anything had been dropped. The bounded list stays
bounded, but it now says how many diagnostics it suppressed, and the health
verdict is still computed from the FULL list, so a cut list cannot turn an
unhealthy answer into a healthy one.

Driven through the public ``wb_card`` tool with the rendered page faked.
"""

from __future__ import annotations

from typing import Any

import pytest
from wb_connector import dom_transport as D
from wb_connector import server


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _patch_dom(monkeypatch: pytest.MonkeyPatch, pages: int) -> None:
    """Every rendered card page is an empty product page (one warning each)."""

    async def fake_render(url: str, js: str, want: str) -> dict[str, Any]:
        return {"title": "", "h1": "", "lines": []}

    monkeypatch.setattr(server._settings, "transport", "dom")
    monkeypatch.setattr(server._settings, "dom_max_cards", 20)
    monkeypatch.setattr(D, "render", fake_render)
    assert pages >= 1


def test_a_cut_warning_list_names_the_number_it_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R18: thirteen diagnostics stay bounded and say that four were dropped."""
    _patch_dom(monkeypatch, pages=12)

    result = _run(server.wb_card(nm_ids=[100000 + index for index in range(12)]))
    warnings = result.meta.warnings

    assert len(warnings) <= 10
    markers = [warning for warning in warnings if "truncat" in warning or "suppress" in warning]
    assert markers, warnings
    # Twelve empty pages give twelve per-card warnings plus one no_items: 13
    # in total, nine kept plus the marker, so four are suppressed. Read the
    # suppressed figure itself, not any digit in the line.
    assert ": 4 further warning(s) suppressed" in markers[0], markers[0]
    assert "13 in total" in markers[0], markers[0]


def test_a_cut_warning_list_does_not_hide_an_unhealthy_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The health verdict comes from the full list, not from the delivered one."""
    _patch_dom(monkeypatch, pages=12)

    result = _run(server.wb_card(nm_ids=[100000 + index for index in range(12)]))

    assert result.meta.healthy is False


def test_a_warning_list_that_fits_is_delivered_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control: nothing is cut, so nothing is said about cutting."""
    _patch_dom(monkeypatch, pages=2)

    result = _run(server.wb_card(nm_ids=[100000, 100001]))

    assert not [warning for warning in result.meta.warnings if "truncat" in warning]
    assert len(result.meta.warnings) == 3  # one per card, plus the empty-result warning
    assert any("nm 100000" in warning for warning in result.meta.warnings)
    assert any("nm 100001" in warning for warning in result.meta.warnings)
