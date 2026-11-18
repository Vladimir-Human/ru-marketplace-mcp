"""Offline regressions for deep-compare ranking, identity, trap and verify guards.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-DEEP-TRAPS across Ozon card-candidate selection, classifieds/condition traps,
         multi-size/model lists, model-token edge cases, non-finite/non-positive price guards and verify fast-fails.
SCOPE: Pure helpers and public compare_prices / compare_verify_offer entries with native response stubs; no network.
DEPENDS: pytest; compare_connector.{decide, identity, models_output, server}; citilink/ozon/taobao output models.
LINKS: M-COMPARE; M-COMPARE-DECIDE; R-COMPARE-DEEP-TRAPS; R-COMPARE-CONDITIONAL-PRICE; R-COMPARE-SIZE-LIST;
       R-COMPARE-MODEL-LIST; R-COMPARE-MODEL-TOKENS; R-COMPARE-VARIANT-IDENTITY; R-NO-FABRICATION.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from compare_connector.decide import (
    ResponseOptions,
    build_response,
    decide_conditional_price,
    pick_card_candidate,
)
from compare_connector.identity import (
    extract_size_tokens,
    lists_models,
    lists_sizes,
    model_mismatch,
    sizes_conflict,
)
from compare_connector.models_output import MarketOffer, SourceOutcome
from fastmcp.exceptions import ToolError
from ozon_connector.models_output import OzonCardResponse, OzonSearchItemOut, OzonSearchResponse
from taobao_connector.models_output import TaobaoSearchItemOut, TaobaoSearchResponse


def _stub_citilink(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, str, float]]) -> None:
    async def search(**kwargs):
        return CitilinkSearchResponse(
            items=[CitilinkSearchItemOut(product_id=pid, title=title, price_rub=price) for pid, title, price in rows]
        )

    monkeypatch.setattr(server, "SOURCES", {"citilink": SimpleNamespace(citilink_search=search)})


# START_BLOCK_OZON_CANDIDATE_TESTS
@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.ozon-candidate", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_ozon_single_card_read_skips_mismatched_model_size_list_and_invalid_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tiles = [
        ("101", "Смартфон Apple iPhone 14 256 ГБ", 45000.0, True),
        ("102", "Смартфон Apple iPhone 16 128 ГБ", 58000.0, True),
        ("103", "Смартфон Apple iPhone 16 128GB/256GB", 59000.0, True),
        ("104", "Смартфон Apple iPhone 15/16 256 ГБ", 60000.0, True),
        ("105", "Аккумулятор для Apple iPhone 16 256GB", 2500.0, True),
        ("106", "Смартфон Apple iPhone 16 256 ГБ", 0.0, True),
        ("107", "Смартфон Apple iPhone 16 256 ГБ", float("nan"), True),
        ("202", "Смартфон Apple iPhone 16 256 ГБ", 72000.0, True),
    ]
    assert pick_card_candidate("iphone 16 256gb", tiles) == "202"

    asked_cards: list[str] = []

    async def ozon_search(**kwargs):
        return OzonSearchResponse(
            items=[
                OzonSearchItemOut(sku=101, title="Смартфон Apple iPhone 14 128 ГБ", price=45000.0, stock="В наличии"),
                OzonSearchItemOut(sku=202, title="Смартфон Apple iPhone 16 256 ГБ", price=72000.0, stock="В наличии"),
            ]
        )

    async def ozon_card(sku_or_path: str) -> OzonCardResponse:
        asked_cards.append(sku_or_path)
        return OzonCardResponse(price=75990.0, card_price=72000.0)

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_search=ozon_search, ozon_card=ozon_card)})
    result = await server.compare_prices("iphone 16 256gb", sources=["ozon"])

    assert asked_cards == ["202"]
    assert result.cheapest_comparable is not None
    assert result.cheapest_comparable.product_id == "202"
    assert result.cheapest_comparable.price_rub == 75990.0


# END_BLOCK_OZON_CANDIDATE_TESTS


# START_BLOCK_CLASSIFIEDS_AND_CONDITION_TRAP_TESTS
@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.classifieds-and-conditions")
@pytest.mark.parametrize(
    "query,trap_title,clean_title",
    [
        ("iphone 16 128gb", "Коробка от Apple iPhone 16 128GB оригинал", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Пустая коробка Apple iPhone 16 128GB", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Муляж Apple iPhone 16 128GB черный", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Макет Apple iPhone 16 128GB для витрины", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Apple iPhone 16 128GB на запчасти", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Apple iPhone 16 128GB в разбор", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Скупка выкуп Apple iPhone 16 128GB", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Обмен Apple iPhone 16 128GB", "Смартфон Apple iPhone 16 128GB"),
        ("playstation 5 slim", "Аренда прокат PlayStation 5 Slim в сутки", "Игровая консоль PlayStation 5 Slim"),
        ("iphone 16 128gb", "Ремонт замена дисплея Apple iPhone 16 128GB", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Замена экрана Apple iPhone 16 128GB", "Смартфон Apple iPhone 16 128GB"),
        ("playstation 5 slim", "Аккаунт PlayStation 5 Slim оффлайн активация", "Игровая консоль PlayStation 5 Slim"),
        ("iphone 16 128gb", "Предзаказ депозит Apple iPhone 16 128GB", "Смартфон Apple iPhone 16 128GB"),
        ("dyson v15 detect", "Детский игрушечный пылесос Dyson V15 Detect", "Вертикальный пылесос Dyson V15 Detect"),
        ("cybex sirona", "Автокресло Cybex Sirona, восстановленное", "Автокресло Cybex Sirona"),
        ("apple airpods pro 2", "Наушники Apple AirPods Pro 2, восстановленные", "Наушники Apple AirPods Pro 2"),
        ("iphone 16 128gb", "Apple iPhone 16 128GB под восстановление", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Apple iPhone 16 128GB неисправный разбитый", "Смартфон Apple iPhone 16 128GB"),
        ("cybex sirona", "Автокресло Cybex Sirona, витринное", "Автокресло Cybex Sirona"),
        ("apple airpods pro 2", "Наушники Apple AirPods Pro 2, уценённые", "Наушники Apple AirPods Pro 2"),
        ("cybex sirona", "Автокресло Cybex Sirona, как новое", "Автокресло Cybex Sirona"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 128GB ресейл", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 128GB после ремонта", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 128GB, б.у.", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 128GB open-box", "Смартфон Apple iPhone 16 128GB"),
        ("iphone 16 128gb", "iPhone 16 128 ГБ, синий, только активирован", "Смартфон Apple iPhone 16 128GB"),
    ],
)
async def test_classifieds_and_condition_traps_never_win_cheapest_comparable(
    monkeypatch: pytest.MonkeyPatch, query: str, trap_title: str, clean_title: str
) -> None:
    _stub_citilink(monkeypatch, [("trap", trap_title, 45000.0), ("clean", clean_title, 65000.0)])

    result = await server.compare_prices(query, sources=["citilink"])

    assert result.cheapest is not None and result.cheapest.product_id == "trap"
    assert result.cheapest_comparable is not None and result.cheapest_comparable.product_id == "clean"


@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.classifieds-and-conditions")
@pytest.mark.parametrize(
    "query,valid_title",
    [
        ("lego 42151", "Конструктор LEGO Technic 42151 Болид Бугатти"),
        ("автокресло cybex sirona", "Детское автокресло Cybex Sirona G i-Size"),
        (
            "iphone 16 128gb",
            "Смартфон Apple iPhone 16 - 128 ГБ - Black - SIM+eSIM, новый, не-активирован, чёрный",
        ),
    ],
)
async def test_hyphenated_negation_and_genuine_category_words_stay_comparable(
    monkeypatch: pytest.MonkeyPatch, query: str, valid_title: str
) -> None:
    _stub_citilink(monkeypatch, [("1", valid_title, 40000.0), ("2", valid_title + " ростест", 45000.0)])

    result = await server.compare_prices(query, sources=["citilink"])

    assert result.cheapest_comparable is not None and result.cheapest_comparable.product_id == "1"


# END_BLOCK_CLASSIFIEDS_AND_CONDITION_TRAP_TESTS


# START_BLOCK_LIST_AND_TOKEN_TESTS
@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.lists-and-tokens", "R-COMPARE-SIZE-LIST")
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Смартфон Xiaomi Redmi Note 14 4G, 6/128ГБ, 6/256ГБ, 8/256ГБ, global", True),
        ("Смартфон Xiaomi Redmi Note 14, 8/128, 8/256 ГБ", True),
        ("Смартфон Xiaomi Redmi Note 14, 6/256, 8/256 ГБ", True),
        ("Смартфон Apple iPhone 16 128-256 ГБ", True),
        ("Смартфон Apple iPhone 16, 8/128ГБ", False),
        ("Ноутбук Apple MacBook Air 13 - 16 ГБ - 512 ГБ - Midnight", False),
    ],
)
def test_aliexpress_ram_rom_and_range_size_lists_are_detected(title: str, expected: bool) -> None:
    assert lists_sizes(title) is expected


@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.lists-and-tokens", "R-COMPARE-VARIANT-IDENTITY")
def test_multi_size_ram_storage_query_requires_every_asked_size_to_match() -> None:
    asked = extract_size_tokens("macbook air m3 16gb 512gb")
    assert sizes_conflict(asked, extract_size_tokens("Ноутбук Apple MacBook Air M3 16 ГБ / 256 ГБ")) is True
    assert sizes_conflict(asked, extract_size_tokens("Ноутбук Apple MacBook Air M3 8 ГБ / 512 ГБ")) is True
    assert sizes_conflict(asked, extract_size_tokens("Ноутбук Apple MacBook Air M3 16 ГБ / 512 ГБ")) is False
    assert sizes_conflict(asked, extract_size_tokens("Ноутбук Apple MacBook Air M3 512 ГБ")) is False


@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.lists-and-tokens", "R-COMPARE-MODEL-LIST")
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Чехол iPhone 15/16", True),
        ("Стекло Galaxy S24/25", True),
        ("Чехол Redmi Note 13/14", True),
        ("Фильтр Dyson V11/15", True),
        ("Ноутбук MacBook Air 8/256", False),
        ("Шина Michelin Pilot Sport 205/55 R16", False),
        ("Монитор 27/32 дюйма", False),
    ],
)
def test_compact_slash_model_lists_are_detected_without_false_positives(title: str, expected: bool) -> None:
    assert lists_models(title) is expected


@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.lists-and-tokens", "R-COMPARE-MODEL-TOKENS")
@pytest.mark.parametrize(
    "query,title,brand,expected_mismatch",
    [
        ("samsung galaxy s25+ 256gb", "Смартфон Samsung Galaxy S25 256 ГБ", "", "missing plus"),
        ("samsung galaxy s25 256gb", "Смартфон Samsung Galaxy S25+ 256 ГБ", "", "s25 plus"),
        ("samsung galaxy s25+ 256gb", "Смартфон Samsung Galaxy S25+ 256 ГБ", "", None),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 128 ГБ SIM+eSIM", "", None),
        ("apple airpods pro 2", "Наушники Apple AirPods Pro, Bluetooth 5.2", "", "missing 2"),
        ("apple airpods 4", "Наушники Apple AirPods, Bluetooth 5.4", "", "missing 4"),
        (
            "кофемашина delonghi magnifica s ecam22.110",
            "Автоматическая кофемашина DeLonghi ECAM22.110. B, 1450 Вт",
            "",
            None,
        ),
        ("apple watch 2", "Умные часы Apple Watch SE 2", "", "watch se"),
        ("ipad 11", "Планшет Apple iPad Air 11", "", "ipad air"),
        ("samsung galaxy z 6", "Смартфон Samsung Galaxy Z Fold 6", "", "z fold"),
        ("samsung galaxy z 6", "Смартфон Samsung Galaxy Z Flip 6", "", "z flip"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 Про 128 ГБ", "", "16 про"),
        ("nintendo switch", "Игровая приставка Nintendo Switch Лайт", "", "switch лайт"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 e 128 ГБ", "", "16 e"),
        ("xiaomi redmi 14", "Смартфон Xiaomi Redmi 14 C 128 ГБ", "", "14 c"),
        ("iphone 16 128gb", "Смартфон Apple iPhone 16 с чехлом в комплекте, 128 ГБ", "", None),
        ("автокресло cybex sirona", "Cybex Sirona G i-Size, автокресло", "", None),
        ("xiaomi 14 256gb", "Смартфон Xiaomi Redmi Note 14 256 ГБ", "", "xiaomi redmi"),
        ("xiaomi 14 256gb", "Смартфон Redmi Note 14 256 ГБ", "Xiaomi", "redmi"),
        ("xiaomi f6 256gb", "Смартфон POCO F6 256 ГБ", "Xiaomi", "poco"),
        ("lego 42151", "Набор подсветки для конструктора Лего 42151", "", "for 42151"),
        ("dyson v15 detect", "Насадка для вертикального беспроводного пылесоса Dyson V15 Detect", "", "for dyson"),
    ],
)
def test_model_mismatch_catches_plus_decimals_suffixes_subbrands_and_for_windows(
    query: str, title: str, brand: str, expected_mismatch: str | None
) -> None:
    assert model_mismatch(query, title, brand) == expected_mismatch


# END_BLOCK_LIST_AND_TOKEN_TESTS


# START_BLOCK_PRICE_AND_VERIFY_GUARD_TESTS
@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.price-and-verify-guards", "R-NO-FABRICATION")
@pytest.mark.parametrize("bad_price", [0.0, -10.0, float("nan"), float("inf"), float("-inf")])
def test_nonpositive_and_nonfinite_prices_never_rank_or_corrupt_spread_or_notes(bad_price: float) -> None:
    coerced = MarketOffer(source="citilink", product_id="bad", title="Kettle", price_rub=bad_price)
    assert coerced.price_rub is None

    bypassed = MarketOffer.model_construct(
        source="citilink",
        product_id="bad",
        title="Kettle",
        price_rub=bad_price,
        currency="rub",
        price_kind="regular",
    )
    valid_low = MarketOffer(source="citilink", product_id="ok1", title="Kettle", price_rub=2000.0)
    valid_high = MarketOffer(source="citilink", product_id="ok2", title="Kettle", price_rub=2500.0)
    response = build_response(
        "Kettle",
        [(SourceOutcome(source="citilink", status="ok"), [bypassed, valid_high, valid_low])],
        ResponseOptions(active=["citilink"], missing=[]),
    )

    assert response.cheapest is not None and response.cheapest.product_id == "ok1"
    assert response.cheapest_comparable is not None and response.cheapest_comparable.product_id == "ok1"
    assert response.price_spread_rub == 500.0

    cond = decide_conditional_price("ozon", "1", 65000.0, bad_price, "card")
    assert (cond.rank_price_rub, cond.note) == (65000.0, None)
    cond_only = decide_conditional_price("ozon", "1", bad_price, 60000.0, "card")
    assert cond_only.rank_price_rub is None
    assert cond_only.note == "conditional_price: ozon/1: 60000 RUB requires Ozon card; not ranked"


@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.price-and-verify-guards")
async def test_verify_offer_fast_fails_cny_expected_price_and_tmall_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def taobao_search(query: str, page: int = 1) -> TaobaoSearchResponse:
        return TaobaoSearchResponse(
            items=[
                TaobaoSearchItemOut(
                    item_id="759336073091",
                    title="Dyson V15 Tmall",
                    price_cny=3299.0,
                    shop_name="dyson旗舰店",
                    url="https://detail.tmall.com/item.htm?id=759336073091",
                ),
                TaobaoSearchItemOut(
                    item_id="992060399536",
                    title="Dyson V15 Taobao",
                    price_cny=2999.0,
                    shop_name="shop",
                    url="https://item.taobao.com/item.htm?id=992060399536",
                ),
            ]
        )

    async def taobao_card(*, item_id_or_url: str):
        calls.append(item_id_or_url)
        return {"item_id": item_id_or_url, "price_cny": 2999.0}

    monkeypatch.setattr(
        server, "SOURCES", {"taobao": SimpleNamespace(taobao_search=taobao_search, taobao_card=taobao_card)}
    )
    offers = await server._search_taobao("dyson v15", 10)
    assert [o.product_id for o in offers] == ["759336073091", "992060399536"]

    with pytest.raises(ToolError, match="CNY"):
        await server.compare_verify_offer("taobao", "992060399536", expected_price_rub=2999.0)
    assert calls == []

    with pytest.raises(ToolError, match="Tmall"):
        await server.compare_verify_offer("taobao", "https://detail.tmall.com/item.htm?id=759336073091")
    with pytest.raises(ToolError, match="Tmall"):
        await server.compare_verify_offer("taobao", "759336073091")
    assert calls == []

    verified = await server.compare_verify_offer("taobao", "992060399536")
    assert calls == ["992060399536"]
    assert verified["card"]["price_cny"] == 2999.0


@pytest.mark.req("R-COMPARE-DEEP-TRAPS", "R-COMPARE-DEEP-TRAPS.price-and-verify-guards")
def test_blank_or_invalid_source_timeout_falls_back_and_pyproject_maps_aliexpress() -> None:
    for bad in (None, "", "   ", "nan", "inf", "-5", "0", "abc"):
        assert server._parse_source_timeout(bad) == 45.0
    assert server._parse_source_timeout("30") == 30.0

    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    uv_sources = pyproject["tool"]["uv"]["sources"]
    assert uv_sources.get("aliexpress-connector") == {"workspace": True}


# END_BLOCK_PRICE_AND_VERIFY_GUARD_TESTS
