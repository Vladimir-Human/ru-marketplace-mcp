"""A title must name the model the query names before it can be cheapest_comparable.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-MODEL-TOKENS on titles recorded in audit cycle 1 (2026-11-17) and on its guards.
SCOPE: identity.model_mismatch alone, and one compare_prices call through the public entry with a stubbed source.
DEPENDS: pytest; compare_connector.identity, compare_connector.server; citilink_connector output models.
LINKS: M-COMPARE; R-COMPARE-MODEL-TOKENS; A7 benchmark (tests/benchmark/labels.json).
END_MODULE_CONTRACT
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from compare_connector.identity import model_mismatch


@pytest.mark.req("R-COMPARE-MODEL-TOKENS")
@pytest.mark.parametrize(
    "query,title,reason",
    [
        # Recorded titles that today's code named cheapest_comparable (A7 cycle 1).
        ("dyson v15 detect", "Батарея Li-Ion RRTGKLL для Dyson V7-V15", "missing detect"),
        ("наушники sony wh-1000xm5", "Беспроводные наушники SONY W919", "missing wh-1000xm5"),
        ("iphone 16 128gb", "Смартфон iPhone 14 128 ГБ, Черный", "missing 16"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16e, 8/128ГБ, global, Б/у", "missing 16"),
        ("lego 42151", "10 шт. шестеренок JIAJIALE 6542 с 16 зубьями для конструкторов GBC", "missing lego"),
        ("lego 42151", "Акриловая витрина для Lego 42151 Bolide", "for lego"),
        ("подгузники pampers premium care 4", "Хлопковые подгузники для взрослых Premium, размер L", "missing pampers"),
        ("playstation 5 slim", "STARTRC GAMES Станция охлаждения для PS5 Slim/Pro", "for ps5"),
        ("xiaomi redmi note 14 256gb", "Смартфон Xiaomi Redmi Note 14S, 8/256ГБ, global", "missing 14"),
        ("ssd samsung 990 pro 1tb", "990EVO PLUS 1TB Внутренний SSD 990PRO без кеша 1 ТБ", "missing samsung"),
        # Constructed: another model right after a model word, an accessory naming the device.
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 Pro, 8/128ГБ", "16 pro"),
        ("xiaomi redmi note 14 256gb", "Смартфон Xiaomi Redmi Note 14 Pro 4G NFC, 8/256 GB", "14 pro"),
        ("iphone 16 pro", "Смартфон Apple iPhone 16 Pro Max, 256 ГБ", "pro max"),
        ("dyson v15 detect", "Аккумулятор для пылесоса dyson V15 detect absolute", "for dyson"),
        # Constructed by MiniMax as attacks on the first version, checked by Claude (2026-11-17).
        ("iphone 16", "Крепление автомобильное, совместимо с iPhone 16", "for iphone"),
        ("samsung ssd 990 pro 1tb", "Корпус для SSD M.2 2280 под Samsung 990 PRO", "for samsung"),
        ("телевизор samsung qe55q60b", "Кронштейн настенный для плоских телевизоров Samsung QE55Q60B", "for samsung"),
        ("dyson v12 detect", "Щётка насадка для пылесоса вертикального Dyson V12 Detect", "for dyson"),
        (
            "кофемашина delonghi ec685",
            "Мерный стакан с крышкой для профессиональной кофемашины Delonghi EC685",
            "for delonghi",
        ),
        ("iphone 16", "Аналог iPhone 16, дисплей и корпус в сборе", "copy"),
        ("lego technic 42151", "Конструктор LEGO Technic 42151, аналог, 1400 деталей", "copy"),
        ("nintendo switch", "Nintendo Switch OLED, 64 ГБ, джойстик", "switch oled"),
    ],
)
def test_a_title_that_is_not_the_queried_model_says_why(query: str, title: str, reason: str) -> None:
    assert model_mismatch(query, title) == reason


@pytest.mark.req("R-COMPARE-MODEL-TOKENS")
@pytest.mark.parametrize(
    "query,title",
    [
        # The labelled winners of A7 cycle 1.
        ("dyson v15 detect", "Вертикальный пылесос dyson V15 detect absolute"),
        ("наушники sony wh-1000xm5", "Наушники беспроводные WH-1000XM5"),  # a code token stands without the brand
        ("iphone 16 128gb", "Смартфон Apple iPhone 16, 8/128ГБ"),
        ("lego 42151", "Конструктор LEGO Technic 42151 Болид Бугатти, жёлтый, 905 дет."),
        ("подгузники pampers premium care 4", "Подгузники Pampers Premium Care для малышей 9-14 кг, 4 размер L, 54 шт"),
        ("playstation 5 slim", "Игровая консоль PS5 Slim цифровая версия - 825 ГБ"),
        ("xiaomi redmi note 14 256gb", "Смартфон Xiaomi Redmi Note 14 4G NFC, 8/256 GB, Черный [Ростест | EAC]"),
        ("ssd samsung 990 pro 1tb", "1000 ГБ M.2 NVMe накопитель Samsung 990 PRO [MZ-V9P1T0CW]"),
        # Guards.
        ("playstation 5 slim", "Игровая приставка Sony PlayStation 5 Slim Digital Edition"),  # one spelling for PS5
        ("наушники сони", "Беспроводные наушники SONY W919"),  # no Latin token: the rule abstains
        ("чехол для iphone 16", "Чехол для iPhone 16, прозрачный"),  # the query asks for the accessory
        ("iphone 16 pro", "Смартфон Apple iPhone 16 Pro, 256 ГБ"),  # the suffix the query names
        ("iphone 16", "Apple/苹果 iPhone 16手机国行正品"),  # a number glued to CJK text is still a whole token
        ("iphone 16", "Смартфон Apple iPhone 16, 128 ГБ, для работы и игр"),  # "для" before no model word
        ("smartphone samsung galaxy a54", "Samsung Galaxy A54 8/128GB, AMOLED 120 Гц"),  # an English category word
        ("laptop lenovo legion pro 5 512gb", "Lenovo Legion Pro 5 16, RTX 4060, 512GB"),
        ("iphone 16 pro max original", "iPhone 16 Pro Max 256GB"),  # a sales word
        ("iphone 16 pro max 256gb", "iPhone 16 Pro Max 256GB для Китая, CN version"),  # a market, not a model
    ],
)
def test_a_title_that_names_the_queried_model_passes(query: str, title: str) -> None:
    assert model_mismatch(query, title) is None


@pytest.mark.req("R-COMPARE-MODEL-TOKENS")
def test_the_offer_brand_field_names_the_brand_a_title_leaves_out() -> None:
    """Wildberries keeps the brand out of the product name (recorded: "Смартфон iPhone 14 128 ГБ", brand Apple)."""
    title = "Смартфон iPhone 16 128 ГБ, черный"

    assert model_mismatch("apple iphone 16 128gb", title) == "missing apple"
    assert model_mismatch("apple iphone 16 128gb", title, brand="Apple") is None
    assert model_mismatch("apple iphone 16 128gb", "Чехол для iPhone 16", brand="Apple") == "for iphone"


def _stub_citilink(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, str, int]]) -> None:
    """Replace ``citilink_search`` with native rows of (id, title, price); the real adapter stays."""

    async def search(**kwargs):
        items = [CitilinkSearchItemOut(product_id=pid, title=title, price_rub=price) for pid, title, price in rows]
        return CitilinkSearchResponse(items=items)

    monkeypatch.setitem(server.SOURCES, "citilink", SimpleNamespace(citilink_search=search))


@pytest.mark.req("R-COMPARE-MODEL-TOKENS")
async def test_compare_prices_skips_another_model_for_cheapest_comparable_but_keeps_it_cheapest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_citilink(
        monkeypatch,
        [("1", "Смартфон Apple iPhone 14, 128 ГБ", 34615), ("2", "Смартфон Apple iPhone 16, 128 ГБ", 64990)],
    )

    result = await server.compare_prices("iphone 16 128gb", sources=["citilink"])

    assert result.cheapest.product_id == "1"
    assert result.cheapest_comparable.product_id == "2"
    assert [w for w in result.warnings if w.startswith("comparable_price:")] == [
        "comparable_price: the raw cheapest listing is an accessory, a different condition, variant or model; "
        "cheapest_comparable is the safer like-for-like candidate"
    ]
