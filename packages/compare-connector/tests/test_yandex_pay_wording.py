"""Yandex's conditional price is named as the page names it: paying with Yandex Pay («Пэй»).

START_MODULE_CONTRACT
PURPOSE: Prove R-YANDEX-PAY-WORDING: the field descriptions, guide text and compare notes call Yandex's
         conditional price the Yandex Pay price (earlier Yandex Plus) and claim no subscription or percentage.
SCOPE: Pydantic field descriptions of the Yandex and compare output models, the yandex guides, the skills,
       and the two compare notes for a Yandex amount.
DEPENDS: compare_connector.decide/models_output; yandex_connector.models_output; mcp_core.resources; pytest.
LINKS: M-YANDEX; M-COMPARE; M-COMPARE-DECIDE; R-YANDEX-PAY-WORDING; audit cycle 1 finding F-B3 (2026-11-17).
END_MODULE_CONTRACT
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from compare_connector.decide import decide_conditional_price
from compare_connector.models_output import MarketOffer
from mcp_core import resources
from yandex_connector.models_output import YandexCardResponse, YandexProduct

ROOT = Path(__file__).resolve().parents[3]
# A subscription claim or a typical discount the 2026-11-17 audit did not see (one card: 2 %).
STALE = re.compile(r"25\s*[-–]\s*30\s*%|Plus subscription|Plus-subscriber|paid subscription \(Yandex Plus\)", re.I)


def _descriptions() -> dict[str, str]:
    return {
        "YandexProduct.price_with_plus": YandexProduct.model_fields["price_with_plus"].description or "",
        "YandexCardResponse.price_with_plus": YandexCardResponse.model_fields["price_with_plus"].description or "",
        "MarketOffer.price_with_subscription_rub": MarketOffer.model_fields["price_with_subscription_rub"].description
        or "",
    }


@pytest.mark.req("R-YANDEX-PAY-WORDING")
def test_the_conditional_yandex_price_fields_name_yandex_pay() -> None:
    for name, text in _descriptions().items():
        assert "Yandex Pay" in text, name
        assert not STALE.search(text), name


@pytest.mark.req("R-YANDEX-PAY-WORDING")
def test_guides_and_skills_claim_no_subscription_or_typical_discount() -> None:
    texts = {f"guide:{key}": resources.GUIDES[key] for key in ("yandex_card", "yandex_search")}
    for rel in (
        "skills/yandex-connector/SKILL.md",
        "skills/compare-prices/SKILL.md",
        "README.en.md",
        "docs/TOOLS.ru.md",
    ):
        texts[rel] = (ROOT / rel).read_text(encoding="utf-8")
    for name, text in texts.items():
        assert not STALE.search(text), name
        assert "подписку Яндекс Плюс" not in text, name


@pytest.mark.req("R-YANDEX-PAY-WORDING")
def test_compare_names_the_yandex_amount_as_the_yandex_pay_price() -> None:
    alone = decide_conditional_price("yandex_market", "1", None, 900.0, "subscription")
    beside = decide_conditional_price("yandex_market", "1", 940.0, 900.0, "subscription")

    assert alone.note == "conditional_price: yandex_market/1: 900 RUB requires paying with Yandex Pay; not ranked"
    assert beside.note == "info:conditional_price_available: yandex_market/1: 900 RUB with Yandex Pay"
