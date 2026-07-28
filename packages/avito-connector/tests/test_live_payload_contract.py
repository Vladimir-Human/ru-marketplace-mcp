"""Contract tests against a REAL ``js/items`` response.

``fixtures/js_items_live.json`` is a genuine Avito search response, captured
2026-07-28 from a residential session (query «ноутбук», locationId 637640). Only
the image lists, the geo block and the description text were trimmed for size;
every field the parser reads is byte-for-byte as Avito sent it.

That capture is the point. Avito answers 403 with a firewall captcha to any
datacenter address, so this connector's payload shape had only ever been asserted
against hand-written dicts — and three of its field aliases turned out not to
exist in the live response at all:

  * ``price`` is absent; the amount lives in ``priceDetailed.value``.
  * the url key is ``urlPath``. The alias list read ``uriPath`` — one letter out —
    so every search hit came back with ``url=None``.
  * there is no ``date``/``time`` string; publication time is ``sortTimeStamp`` in
    epoch milliseconds, so ``posted_at`` was null on every row.

And the crash this fixture exists to prevent: ``location`` arrives as an object
(``{"id": 637640, "name": "Москва", ...}``) against a ``str | None`` field, which
made Pydantic reject the whole page.
"""

from __future__ import annotations

import json
from pathlib import Path

from avito_connector.models_output import AvitoSearchItemOut
from avito_connector.server import _parse_search_items, _posted_at

FIXTURE = Path(__file__).parent / "fixtures" / "js_items_live.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _items() -> list[AvitoSearchItemOut]:
    raw, _total = _parse_search_items(_payload())
    return [AvitoSearchItemOut(**row) for row in raw]


def test_live_payload_validates_at_all() -> None:
    """The original bug: one nested field took down the entire page of listings."""
    items = _items()
    assert len(items) == 3


def test_location_object_becomes_a_place_name() -> None:
    """``location`` is an object upstream; the wire field is a string."""
    items = _items()
    assert items[0].location == "Москва"
    assert items[1].location == "Москва"


def test_a_place_name_is_never_a_python_repr() -> None:
    """``str(dict)`` would pass validation and show the user Python syntax.

    Worse than the crash it replaces, because nothing downstream can tell it is
    not a city.
    """
    for item in _items():
        if item.location is not None:
            assert "{" not in item.location
            assert "'id'" not in item.location


def test_missing_place_name_is_none_not_invented() -> None:
    """Item 3 has ``location: null`` and ``addressDetailed.locationName: ""``.

    It also carries ``locationId: 625810``. Resolving that id to a city name would
    require a lookup table this connector does not have, so the honest answer is
    None — not a guess that reads like data.
    """
    items = _items()
    assert items[2].location is None


def test_price_comes_from_price_detailed_value() -> None:
    """There is no top-level ``price`` key in the live response."""
    assert [i.price_rub for i in _items()] == [15500.0, 15990.0, 8700.0]
    assert "price" not in _payload()["items"][0]


def test_every_item_has_a_url() -> None:
    """Regression for the ``uriPath`` / ``urlPath`` alias miss.

    A search result with no URL cannot be opened, cannot be passed to
    ``avito_card``, and gives ``compare_prices`` an offer with no link.
    """
    for item in _items():
        assert item.url, "search hit has no url — the urlPath alias regressed"
        assert item.url.startswith("https://www.avito.ru/")


def test_posted_at_is_iso_not_a_bare_epoch() -> None:
    """``sortTimeStamp`` is epoch ms; a 13-digit number is indistinguishable from an id."""
    for item in _items():
        assert item.posted_at is not None
        assert item.posted_at.endswith("Z")
        assert item.posted_at.startswith("202")


def test_posted_at_handles_a_seconds_based_drift() -> None:
    """If upstream ever switches to seconds, do not land in 1970 or the year 57000."""
    assert _posted_at({"sortTimeStamp": 1782893206000}).startswith("2026-")
    assert _posted_at({"sortTimeStamp": 1782893206}).startswith("2026-")


def test_posted_at_prefers_an_explicit_string() -> None:
    """A real date string, when Avito sends one, wins over the epoch field."""
    assert _posted_at({"date": "2026-07-20 11:02:00", "sortTimeStamp": 1782893206000}) == "2026-07-20 11:02:00"


def test_posted_at_refuses_junk() -> None:
    assert _posted_at({}) is None
    assert _posted_at({"sortTimeStamp": True}) is None
    assert _posted_at({"sortTimeStamp": "not a stamp"}) is None


def test_total_count_is_read_from_the_envelope() -> None:
    _raw, total = _parse_search_items(_payload())
    assert total == 51343


def test_absent_seller_stays_none() -> None:
    """These rows carry no seller object — only ``userLogo``.

    Reporting None is correct. Deriving a name from the logo link would be a guess
    dressed as a seller.
    """
    for item in _items():
        assert item.seller_name is None
