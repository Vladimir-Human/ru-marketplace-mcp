"""AliExpress names longer than 140 characters stay the title (audit cycle 2, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove R-ALIEXPRESS-LONG-TITLE with the real search extractor in jsdom: a 146-163 character name is the title,
         and a sales count, a delivery line or the "Рекомендуем" badge never becomes one (None when no name is there).
SCOPE: aliexpress_connector.server._SEARCH_EXTRACT_JS over a constructed grid in the shape of the recorded tiles.
DEPENDS: pytest; mcp_core.domtest (jsdom); aliexpress_connector.server.
LINKS: M-ALIEXPRESS; R-ALIEXPRESS-LONG-TITLE.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aliexpress_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

# The leaf texts of three tiles the cycle-2 recording titled "до 10 дней, бесплатно" and "20 купили".
LONG = (
    "Кроссовки Adidas Handball Spezial, коричневые, из замши, в ретро-стиле, с белыми полосками, "
    "классическая повседневная модель для ежедневной носки."
)
LONGER = (
    "AD - тренировочные кроссовки ORIGINALS SAMBA OG SHOES. Классическая модель с замшевыми вставками, "
    "резиновая подошва, удобная колодка для города и спорта, унисекс"
)
TILES = {
    "1005013572245832": ["20 918 ₽", "-60%", "8 369 ₽", LONG, "Рекомендуем", "до 10 дней, бесплатно"],
    "1005006734119032": ["11 397,25 ₽", "-80%", "2 279,45 ₽", "5.0", "20 купили", LONGER, "бесплатно"],
    "1005010508835558": ["275 ₽", "-16%", "232 ₽", "3 купили", "Рекомендуем", "до 10 дней, бесплатно"],
}


def _grid(tmp_path: Path) -> Path:
    tiles = "".join(
        f'<div class="tile"><a href="https://aliexpress.ru/item/{item_id}.html"><div>'
        + "".join(f"<div>{text}</div>" for text in leaves)
        + "</div></a></div>"
        for item_id, leaves in TILES.items()
    )
    path = tmp_path / "grid.html"
    path.write_text(f"<html><head><title>AliExpress</title></head><body><div>{tiles}</div></body></html>", "utf-8")
    return path


@pytest.mark.req("R-ALIEXPRESS-LONG-TITLE")
def test_a_long_name_is_the_title_and_tile_lines_never_are(tmp_path: Path) -> None:
    assert len(LONG) > 140 and len(LONGER) > 140
    try:
        raw = run_extractor(server._SEARCH_EXTRACT_JS, _grid(tmp_path), page_url="https://aliexpress.ru/wholesale")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    payload = raw if isinstance(raw, dict) else json.loads(raw)

    titles = {item["item_id"]: item["title"] for item in payload["items"]}

    assert titles == {"1005013572245832": LONG, "1005006734119032": LONGER, "1005010508835558": None}
