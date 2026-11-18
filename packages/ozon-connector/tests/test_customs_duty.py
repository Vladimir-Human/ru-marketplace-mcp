"""Cross-border Ozon cards carry a customs payment due on delivery (GitHub issue #329, 2026-11-18).

START_MODULE_CONTRACT
PURPOSE: Prove R-OZON-CUSTOMS-DUTY through the public ozon_card: the customsDuty widget gives customs_duty_rub and a
         customs_duty warning; price stays the checkout price; other cards and other icon widgets change nothing.
SCOPE: ozon_card with _fetch_composer replaced by a composer payload in the shape recorded on 2026-11-18
       (webIconWithText widget, params.mode customsDuty, textRs amount "~8 167 ₽ " then "доплата за таможенный платёж").
DEPENDS: pytest; ozon_connector.server.
LINKS: M-OZON; R-OZON-CUSTOMS-DUTY.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json

import pytest
from ozon_connector import server


def _duty_widget(amount_text: str, mode: str = "customsDuty") -> str:
    return json.dumps(
        {
            "textRs": [
                {"type": "text", "content": amount_text},
                {"type": "text", "content": "доплата за таможенный платёж"},
            ],
            "link": "/modal/customs-duty?product_id=1980467719",
            "params": {"mode": mode},
        },
        ensure_ascii=False,
    )


def _payload(*extra: tuple[str, str]) -> str:
    states = {
        "webPrice-3121879-default-1": json.dumps(
            {"isAvailable": True, "price": "68 070 ₽", "cardPrice": "64 503 ₽", "originalPrice": "79 990 ₽"},
            ensure_ascii=False,
        ),
        "webProductHeading-3385933-default-1": json.dumps({"title": "Смартфон Example X 256 ГБ"}, ensure_ascii=False),
    }
    states.update(dict(extra))
    return json.dumps({"widgetStates": states}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()


def _serve(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    async def fetch(api_path, ctx):
        return 200, body, "cdp"

    monkeypatch.setattr(server, "_fetch_composer", fetch)


def _duty_warnings(card) -> list[str]:
    return [warning for warning in card.meta.warnings if warning.startswith("customs_duty:")]


@pytest.mark.req("R-OZON-CUSTOMS-DUTY")
async def test_the_customs_widget_gives_the_duty_and_a_warning(monkeypatch) -> None:
    _serve(monkeypatch, _payload(("webIconWithText-3411342-default-1", _duty_widget("~8 167 ₽ "))))

    card = await server.ozon_card("1980467719")

    assert card.customs_duty_rub == 8167.0
    assert card.price == 68070.0  # the checkout price is not changed
    [warning] = _duty_warnings(card)
    assert "8 167" in warning and "not in price" in warning


@pytest.mark.req("R-OZON-CUSTOMS-DUTY")
async def test_an_unreadable_amount_still_warns(monkeypatch) -> None:
    _serve(monkeypatch, _payload(("webIconWithText-3411342-default-1", _duty_widget("уточняется"))))

    card = await server.ozon_card("1980467719")

    assert card.customs_duty_rub is None
    [warning] = _duty_warnings(card)
    assert "amount not stated" in warning


@pytest.mark.req("R-OZON-CUSTOMS-DUTY")
@pytest.mark.parametrize(
    "extra",
    [
        (),  # a domestic card
        (("webIconWithText-1-default-1", _duty_widget("~500 ₽ ", mode="delivery")),),  # another icon line
    ],
    ids=["domestic", "other-icon"],
)
async def test_no_customs_widget_changes_nothing(monkeypatch, extra) -> None:
    _serve(monkeypatch, _payload(*extra))

    card = await server.ozon_card("1980467719")

    assert card.customs_duty_rub is None
    assert _duty_warnings(card) == []
    assert card.price == 68070.0
