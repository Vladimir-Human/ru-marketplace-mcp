"""Wildberries read through the operator's Chrome (``WB_TRANSPORT=dom``).

Why this exists
---------------
Measured 2026-09-30 from a residential Moscow address, in a clean scraping
profile that had passed WB's own browser check:

* ``card.wb.ru`` / ``search.wb.ru`` / ``catalog.wb.ru`` answer 403 to every
  client, including a real Chrome navigating to them directly;
* the site's same-origin API (``/__internal/card/...``, ``/__internal/search/...``,
  ``/__internal/u-search/...``) answers 403 to a plain ``fetch`` issued from a
  wildberries.ru tab — the site's frontend adds something a bare request lacks;
* the *rendered pages* are readable: the search grid carries ``[data-nm-id]``
  tiles, and the product page carries three prices, the store, the legal
  entity, delivery, warehouse and the return policy.

So this transport reads what WB already rendered for a real browser session,
the same way the DNS and Citilink connectors do. It deliberately does NOT call
WB's API, replay or forge tokens, alter the browser fingerprint, or solve
challenges:

* «Проверяем браузер» is WB's automatic check — we wait for it to clear;
* «Подозрительная активность» / a captcha is a stop: we raise, never retry,
  and the operator decides what to do.

Price semantics (confirmed on nm 1345073040, 2026-09-30)
-------------------------------------------------------
Product page: ``4 961 ₽`` (red, wallet icon) = WB Wallet price, ``5 063 ₽`` =
regular card price, ``9 338 ₽`` (struck) = marketing "before discount" price.
Search tile: only the Wallet price and the struck price are shown, followed by
the label «с WB Кошельком». ``price_rub`` is therefore the regular price when
the page shows it, and the Wallet price otherwise — ``price_kind`` says which.

Parsing is pure Python over plain text lines so it can be tested offline and
survives CSS class churn; the JS side only collects text.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
from typing import Any

from mcp_core.cache import TTLCache
from mcp_core.errors import (
    ChallengeRequiredError,
    ParserDriftError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.redact import redact_error_text as _redact

from wb_connector.settings import get_settings

_settings = get_settings()

SITE = "https://www.wildberries.ru"
ALLOWED_HOSTS = frozenset({"www.wildberries.ru", "wildberries.ru"})
MAX_PAYLOAD_BYTES = 2_000_000

# A Wallet price is a small discount on the regular price (2 % on the reference
# card). When only one other price accompanies it, a ratio above this bound
# means that price is the struck "before discount" figure, not the regular one.
WALLET_GAP_MAX = 1.15

# ---------------------------------------------------------------------------
# JavaScript: collect text only. All interpretation happens in Python.
# ---------------------------------------------------------------------------

PROBE_JS = r"""
() => {
  const full = (document.body && document.body.innerText) || '';
  const body = full.slice(0, 4000);
  const title = document.title || '';
  const hay = (title + '\n' + body).toLowerCase();
  // A captcha widget counts only when it is actually on screen: hidden
  // pre-rendered containers must not stop every read.
  const visible = sel => [...document.querySelectorAll(sel)].some(e => e.offsetParent !== null && e.getBoundingClientRect().height > 20);
  const captcha = visible('iframe[src*="captcha" i], [class*="captcha" i], [id*="captcha" i]') ||
    /я не робот|введите символы с картинки/.test(hay);
  let state = 'ok';
  if (captcha || /подозрительная активность/.test(hay)) state = 'blocked';
  else if (/проверяем браузер|проверка браузера/.test(hay)) state = 'challenge';
  return JSON.stringify({
    state: state,
    captcha: captcha,
    tiles: document.querySelectorAll('[data-nm-id]').length,
    h1: !!document.querySelector('h1'),
    sku: /Артикул/.test(full.slice(0, 20000)),
    // «Все 27 предложений от 7 369 ₽» (one offer: «1 предложение от 4 735 ₽») renders after the store block, and only on cards with other sellers.
    offers: /(?:Все\s+)?\d+\s+предложени\S*\s+от/i.test(full.slice(0, 30000)),
    // The store block («Находки из Китая 5,0») renders after prices and «Артикул».
    store: [...document.querySelectorAll('[class*="seller" i]')].some(e => {
      const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      return /\d[.,]\d$/.test(t) && !/стать продавцом|смотрите также/i.test(t);
    }),
    rub: body.indexOf('₽') >= 0,
    empty: /ничего не нашлось|ничего не найдено|по вашему запросу ничего/.test(hay),
    missing: /такой страницы нет|страница не найдена|товар не найден/.test(hay)
  });
}
"""

SEARCH_EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const seen = new Set();
  const tiles = [];
  for (const el of document.querySelectorAll('[data-nm-id]')) {
    const nm = el.getAttribute('data-nm-id');
    if (!nm || seen.has(nm)) continue;
    seen.add(nm);
    const lines = (el.innerText || '').split('\n').map(clean).filter(Boolean).slice(0, 30).map(s => s.slice(0, 300));
    const a = el.querySelector('a[href*="/catalog/"]');
    const label = a ? clean(a.getAttribute('aria-label') || a.getAttribute('title') || '') : '';
    const pick = sel => [...el.querySelectorAll(sel)].map(e => clean(e.innerText)).filter(t => t.indexOf('₽') >= 0).slice(0, 3);
    tiles.push({nm: nm, lines: lines, label: label.slice(0, 300), del: pick('del, s'), wallet: pick('[class*="wallet" i]')});
    if (tiles.length >= 60) break;
  }
  return JSON.stringify({title: document.title || '', url: location.href, tiles: tiles});
}
"""

CARD_EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const root = document.querySelector('main') || document.body;
  const lines = ((root && root.innerText) || '').split('\n').map(clean).filter(Boolean).slice(0, 400).map(s => s.slice(0, 300));
  const pick = (sel, n) => [...document.querySelectorAll(sel)].map(e => clean(e.innerText)).filter(Boolean).slice(0, n).map(s => s.slice(0, 200));
  const h1 = document.querySelector('h1');
  // «Все N предложений»: other sellers' tiles. The block is found by its link to
  // /catalog/<nm>/other-sellers; each tile is the widest ancestor of one
  // /catalog/<nm>/detail.aspx link that holds no second tile's link. Their text
  // and nm ids go out as `offers`, so Python can keep them apart from the card.
  const offers = [];
  const more = document.querySelector('a[href*="/other-sellers"]');
  let block = more;
  const detail = 'a[href*="/detail.aspx"]';
  while (block && !block.querySelector(detail) && block.parentElement) block = block.parentElement;
  if (block && block.querySelector(detail)) {
    const hrefs = a => a.getAttribute('href') || '';
    const nmOf = a => { const m = /\/catalog\/(\d+)\/detail\.aspx/.exec(hrefs(a)); return m ? m[1] : ''; };
    const seen = new Set();
    for (const a of block.querySelectorAll(detail)) {
      const nm = nmOf(a);
      if (!nm || seen.has(nm)) continue;
      seen.add(nm);
      let tile = a;
      while (tile.parentElement && tile.parentElement !== block &&
             new Set([...tile.parentElement.querySelectorAll(detail)].map(nmOf)).size === 1) tile = tile.parentElement;
      const tl = (tile.innerText || '').split('\n').map(clean).filter(Boolean).slice(0, 12).map(s => s.slice(0, 200));
      offers.push({nm: nm, lines: tl});
      if (offers.length >= 12) break;
    }
  }
  return JSON.stringify({
    title: document.title || '',
    url: location.href,
    h1: h1 ? clean(h1.innerText) : '',
    lines: lines,
    wallet: pick('[class*="wallet" i]', 8).filter(t => t.indexOf('₽') >= 0),
    del: pick('del, s', 8).filter(t => t.indexOf('₽') >= 0),
    seller: pick('[class*="seller" i]', 10).filter(t => !/стать продавцом/i.test(t)),
    offers: offers
  });
}
"""

# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------

_SP = "[ \u00a0\u2009\u202f]"
_PRICE_RE = re.compile(rf"(?<![\d.,])(\d{{1,3}}(?:{_SP}\d{{3}})+|\d+)(?:[.,]\d{{1,2}})?{_SP}*₽(?!{_SP}*/{_SP}*мес)")
_MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
_DELIVERY_RE = re.compile(
    rf"^(сегодня|завтра|послезавтра|\d{{1,2}}(?:{_SP}*[–-]{_SP}*\d{{1,2}})?{_SP}+(?:{_MONTHS}))(?:{_SP}*,{_SP}*(.*))?$",
    re.IGNORECASE,
)
_RATING_INLINE_RE = re.compile(
    rf"^(\d(?:[.,]\d)?){_SP}*[·•]{_SP}*(\d[\d \u00a0\u2009\u202f]*){_SP}+оцен", re.IGNORECASE
)  # «5 · 222 оценки»; the separator is mandatory, so «222 оценки» alone never reads as «2» + «22»
_RATING_INLINE_DEC_RE = re.compile(rf"^(\d[.,]\d){_SP}+(\d[\d \u00a0\u2009\u202f]*){_SP}+оцен", re.IGNORECASE)
_SEPARATOR_LINE = frozenset({"·", "•"})
_OFFERS_RE = re.compile(
    rf"(?:Все{_SP}+)?(\d+){_SP}+предложени\w*\s+от{_SP}+(\d{{1,3}}(?:{_SP}\d{{3}})+|\d+){_SP}*₽", re.IGNORECASE
)
OTHER_OFFERS_ANOMALY = 0.6  # other sellers «от» below 60 % of this card's price → price anomaly
_RATING_ONLY_RE = re.compile(r"^(\d(?:[.,]\d)?)$")
_STORE_RATING_ONLY_RE = re.compile(r"^(\d[.,]\d)$")
_COUNT_ONLY_RE = re.compile(rf"^[·•]?{_SP}*(\d[\d \u00a0\u2009\u202f]*){_SP}+оцен", re.IGNORECASE)
_BRAND_NAME_RE = re.compile(r"^(.{1,80}?) / (.+)$")
_STORE_RATING_RE = re.compile(r"^(.*?)[\s★]*(\d[.,]\d)?\s*$")
_COUNTRY_RE = re.compile(r"^([A-Z]{2})\s*,")
_BUY_LINES = frozenset({"купить сейчас", "добавить в корзину", "в корзину", "купить"})
_OUT_OF_STOCK_RE = re.compile(r"нет в наличии|товар закончился|сообщить о поступлении|нет на складе", re.IGNORECASE)
_NO_BRAND = {"нет бренда", "без бренда", ""}
_CARD_LABELS = (
    "Артикул",
    "Гарантийный срок",
    "Наименование продавца",
    "Адрес продавца",
    "Номер регистрации",
    "ИНН",
    "ОГРНИП",
    "ОГРН",
)


def prices_in(text: str) -> list[float]:
    """Every ruble amount in ``text``, in order; instalments («… ₽/мес») excluded."""
    out: list[float] = []
    for match in _PRICE_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", match.group(1))
        if digits:
            value = float(int(digits))
            if value > 0:
                out.append(value)
    return out


def _dedupe(values: list[float]) -> list[float]:
    return list(dict.fromkeys(v for v in values if v and v > 0))


def assign_prices(
    prices: list[float],
    *,
    wallet_hint: float | None = None,
    crossed_hint: float | None = None,
    wallet_label: bool = False,
) -> tuple[float | None, float | None, float | None]:
    """Split the price figures of one offer into (regular, wallet, crossed).

    WB renders them ascending: wallet < regular < struck. The struck price is
    settled first (a ``<del>`` hint, else the largest of two or more figures;
    with a known Wallet price and one other figure, that figure is struck only
    when it is far above the Wallet price). Of what stays below it, two figures
    are (wallet, regular); a single one is the Wallet price when the tile says
    «с WB Кошельком», otherwise the regular price.

    Known blind spot: a page showing only wallet + regular, with no struck
    price and no label, reads as (regular, struck). WB practically always shows
    a struck price, and wb_card on the product page resolves the three figures.
    """
    ps = _dedupe(prices)
    wallet, crossed = wallet_hint, crossed_hint
    if crossed is None:
        others = [p for p in ps if p != wallet]
        if wallet is not None and len(others) == 1:
            if others[0] > wallet * WALLET_GAP_MAX:
                crossed = others[0]
        elif len(others) >= 2:
            crossed = max(others)
    below = sorted(
        p
        for p in ps
        if p != wallet and p != crossed and (crossed is None or p < crossed) and (wallet is None or p > wallet)
    )
    regular: float | None = None
    if wallet is None:
        if len(below) >= 2:
            wallet, regular = below[0], below[1]
        elif below:
            if wallet_label:
                wallet = below[0]
            else:
                regular = below[0]
    elif below:
        regular = below[0]
    return regular, wallet, crossed


def _first_price(texts: Any, among: list[float] | None = None) -> float | None:
    """First price in ``texts``; with ``among``, the first one that is also an offer figure.

    Page-wide selectors (``del``, ``[class*=wallet]``) also match the «Смотрите
    также» carousel: on 2026-09-30 the first ``<del>`` on the product page was a
    neighbour's 6 415 ₽, not this card's 9 338 ₽. A hint is trusted only when it
    names a figure the offer itself shows.
    """
    if not isinstance(texts, list):
        return None
    for text in texts:
        if isinstance(text, str):
            for price in prices_in(text):
                if among is None or price in among:
                    return price
    return None


def _str_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [line for line in value if isinstance(line, str) and line.strip()]


def _clean_brand(brand: str) -> str:
    brand = brand.strip()
    return "" if brand.lower() in _NO_BRAND else brand


def _brand_name(lines: list[str], fallback_name: str = "") -> tuple[str, str]:
    """(brand, name) from a «Brand / Name» pair — on one line or split over lines."""
    for i, line in enumerate(lines):
        if "₽" in line:
            continue
        match = _BRAND_NAME_RE.match(line)
        if match:
            return _clean_brand(match.group(1)), match.group(2).strip()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line == "/" and i > 0 and nxt and "₽" not in lines[i - 1]:
            return _clean_brand(lines[i - 1]), nxt.strip()
        if line.endswith(" /") and nxt and "₽" not in nxt:
            return _clean_brand(line[:-2]), nxt.strip()
        if line.startswith("/ ") and i > 0 and "₽" not in lines[i - 1]:
            return _clean_brand(lines[i - 1]), line[2:].strip()
    return "", fallback_name


def parse_rating(lines: list[str]) -> tuple[float | None, int | None]:
    """Product rating and rating count, from the first rating block in ``lines``.

    Forms seen live: «5 · 1 оценка» on one line; «5» + «· 1 оценка»;
    «5» + «·» + «222 оценки»; «Нет оценок».
    """
    for i, line in enumerate(lines):
        low = line.lower()
        if low.startswith("нет оценок") or low.startswith("отзывов пока нет"):
            return None, 0
        match = _RATING_INLINE_RE.match(line) or _RATING_INLINE_DEC_RE.match(line)
        if match:
            return float(match.group(1).replace(",", ".")), int(re.sub(r"\D", "", match.group(2)))
        only = _RATING_ONLY_RE.match(line)
        if not only:
            continue
        j = i + 1
        if j < len(lines) and lines[j].strip() in _SEPARATOR_LINE:
            j += 1
        if j < len(lines):
            count = _COUNT_ONLY_RE.match(lines[j])
            if count:
                return float(only.group(1).replace(",", ".")), int(re.sub(r"\D", "", count.group(1)))
    return None, None


def parse_delivery(lines: list[str]) -> tuple[str | None, str | None, int | None]:
    """(delivery date text, warehouse text, index of the last line used) — first match only."""
    for i, line in enumerate(lines):
        match = _DELIVERY_RE.match(line)
        if not match:
            continue
        warehouse = (match.group(2) or "").strip() or None
        last = i
        if warehouse is None and line.rstrip().endswith(",") and i + 1 < len(lines):
            nxt = lines[i + 1]
            if "склад" in nxt.lower() or "маркетплейс" in nxt.lower():
                warehouse, last = nxt.strip(), i + 1
        return match.group(1), warehouse, last
    return None, None, None


def parse_search_tile(tile: dict[str, Any]) -> dict[str, Any] | None:
    """One ``[data-nm-id]`` tile → a ``WbCardItem``-shaped dict, or None."""
    raw_nm = str(tile.get("nm") or "")
    if not raw_nm.isdigit():
        return None
    lines = _str_lines(tile.get("lines"))
    raw_label = tile.get("label")
    label = raw_label if isinstance(raw_label, str) else ""
    price_lines = [line for line in lines if "мес" not in line.lower()]
    prices: list[float] = []
    for line in price_lines:
        prices.extend(prices_in(line))
    wallet_label = any("кошельк" in line.lower() for line in lines)
    regular, wallet, crossed = assign_prices(
        prices,
        wallet_hint=_first_price(tile.get("wallet"), prices),
        crossed_hint=_first_price(tile.get("del"), prices),
        wallet_label=wallet_label,
    )
    brand, name = _brand_name(lines, fallback_name=label)
    rating, feedbacks = parse_rating(lines)
    delivery, warehouse, _ = parse_delivery(lines)
    price = regular if regular is not None else wallet
    return {
        "nm_id": int(raw_nm),
        "name": name,
        "brand": brand,
        "review_rating": rating,
        "feedbacks": feedbacks,
        "total_quantity": None,
        "in_stock": price is not None,
        "price_rub": price,
        "price_original_rub": crossed,
        "wallet_price_rub": wallet,
        "price_kind": "regular" if regular is not None else ("wallet" if wallet is not None else ""),
        "delivery": delivery,
        "warehouse": warehouse,
        "cross_border": True if any(line.strip().lower() == "aliexpress" for line in lines) else None,
        "transport": "dom",
    }


def parse_search_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    raw_tiles = payload.get("tiles")
    tiles: list[Any] = raw_tiles if isinstance(raw_tiles, list) else []
    items = [item for item in (parse_search_tile(t) for t in tiles if isinstance(t, dict)) if item]
    warnings: list[str] = []
    if items and any(item["price_kind"] == "wallet" for item in items):
        warnings.append(
            "wb_dom_search: price_rub is the WB Wallet price on search tiles (the regular card price is "
            "equal or a few % higher and shows only on the product page — verify leaders with wb_card)"
        )
    if items and all(item["price_rub"] is None for item in items):
        warnings.append("wb_dom_search: no prices read from any tile — DOM shape may have moved")
    return items, warnings


def _label_values(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for i, line in enumerate(lines):
        for label in _CARD_LABELS:
            if label in found or not line.startswith(label):
                continue
            rest = line[len(label) :]
            if rest and rest[0].isalpha():
                continue  # «ИННОВАЦИЯ» is not «ИНН»
            value = rest.lstrip(" .:\t…·").strip()  # dotted leaders sit between label and value
            if not value and i + 1 < len(lines):
                value = lines[i + 1].strip()
            if value:
                found[label] = value
            break
    return found


_NAME_LIKE_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")
_NOT_A_STORE = re.compile(
    r"^(смотрите также|стать продавцом|похожие|все товары|в каталог|оценки|отзыв|поделитесь|розыгрыш)", re.IGNORECASE
)
_STORE_WITH_RATING_RE = re.compile(r"^(.+?)[\s★]*(\d[.,]\d)$")


def _store_from_buy_block(tail: list[str], delivery_index: int | None) -> tuple[str, float | None]:
    """The store sits right under «11 октября, склад продавца» in the buy block, its rating below."""
    if delivery_index is None or delivery_index + 1 >= len(tail):
        return "", None
    name = tail[delivery_index + 1].strip()
    if not name or "₽" in name or _NOT_A_STORE.match(name) or _DELIVERY_RE.match(name):
        return "", None
    rating = None
    if delivery_index + 2 < len(tail) and _STORE_RATING_ONLY_RE.match(tail[delivery_index + 2]):
        rating = float(tail[delivery_index + 2].replace(",", "."))
    return name, rating


def _store_from_seller_texts(texts: list[str]) -> tuple[str, float | None]:
    """Fallback: first page-wide «seller» text that reads «Name 4,9» and is not a section heading."""
    for text in texts:
        match = _STORE_WITH_RATING_RE.match(text.strip())
        if match and not _NOT_A_STORE.match(match.group(1)):
            return match.group(1).strip(), float(match.group(2).replace(",", "."))
    for text in texts:
        if text.strip() and not _NOT_A_STORE.match(text.strip()) and "₽" not in text:
            return text.strip(), None
    return "", None


def _name_from_title(title: Any, nm: int) -> str:
    """«Name Brand 1345073040» → «Name Brand»: WB appends the nm id to the tab title."""
    if not isinstance(title, str):
        return ""
    text = title.strip()
    if text.endswith(str(nm)):
        text = text[: -len(str(nm))].strip()
    return "" if text.lower().startswith("интернет") else text


_OFFERS_HEAD_RE = re.compile(rf"^(?:Все{_SP}+)?\d+{_SP}+предложени\w*{_SP}+от{_SP}", re.IGNORECASE)
_OFFER_RATING_RE = re.compile(r"^(нет оценок|\d[.,]?\d?)$", re.IGNORECASE)
MAX_OTHER_OFFERS = 5


def _parse_other_offers(raw: Any, requested_nm: int) -> list[dict[str, Any]]:
    """The «Все N предложений» tiles → up to five cheapest, as ``WbOtherOffer``-shaped dicts.

    A tile reads «7 413 ₽ / Нет оценок / 11 октября / UJII»: price, rating, delivery, store.
    The page does not say whether that price is the regular or the Wallet one (the tile has
    no Wallet label), so ``price_kind`` stays empty unless the tile itself names the Wallet.
    """
    offers: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        nm_text = re.sub(r"\D", "", str(entry.get("nm") or ""))
        lines = _str_lines(entry.get("lines"))
        if not nm_text or int(nm_text) == requested_nm or int(nm_text) in seen:
            continue
        prices = [p for line in lines if "мес" not in line.lower() for p in prices_in(line)]
        if not prices:
            continue
        seen.add(int(nm_text))
        delivery = next((m.group(1) for m in (_DELIVERY_RE.match(line) for line in lines) if m), None)
        supplier = next(
            (
                line
                for line in reversed(lines)
                if "₽" not in line and not _OFFER_RATING_RE.match(line) and not _DELIVERY_RE.match(line)
            ),
            "",
        )
        wallet = any("кошельк" in line.lower() for line in lines)
        offers.append(
            {
                "nm_id": int(nm_text),
                "supplier": supplier,
                "price_rub": prices[0],
                "price_kind": "wallet" if wallet else "",
                "delivery": delivery,
            }
        )
    offers.sort(key=lambda offer: offer["price_rub"])
    return offers[:MAX_OTHER_OFFERS]


def _without_offers_block(lines: list[str], raw_offers: Any) -> list[str]:
    """``lines`` minus the «Все N предложений» block, so no other seller's text reads as this card's."""
    start = next((i for i, line in enumerate(lines) if _OFFERS_HEAD_RE.match(line)), None)
    if start is None:
        return lines
    end = start + 1
    if end < len(lines) and lines[end].strip().lower() == "все":  # the «Все ›» button
        end += 1
    for entry in raw_offers if isinstance(raw_offers, list) else []:
        for text in _str_lines(entry.get("lines")) if isinstance(entry, dict) else []:
            hit = next((j for j in range(end, min(len(lines), end + 12)) if lines[j] == text), None)
            if hit is not None:
                end = hit + 1
    return lines[:start] + lines[end:]


def parse_card_page(payload: dict[str, Any], requested_nm: int) -> tuple[dict[str, Any] | None, list[str]]:
    """Product page → ``WbCardItem``-shaped dict (or None) plus warnings."""
    warnings: list[str] = []
    lines = _str_lines(payload.get("lines"))
    raw_h1 = payload.get("h1")
    h1 = raw_h1.strip() if isinstance(raw_h1, str) else ""
    if not _NAME_LIKE_RE.search(h1):
        # Product pages carry no title <h1>; once the reviews widget renders, its «5,0»
        # score is the page's only <h1> (live, 2026-09-30). A name has words in it.
        h1 = ""
    labels = _label_values(lines)
    head_brand, head_name = _brand_name(lines[:40])
    name = head_name or h1 or _name_from_title(payload.get("title"), requested_nm)
    if not lines or (not name and "Артикул" not in labels):
        return None, [f"wb_dom_card: nm {requested_nm} rendered no product (delisted or DOM moved)"]
    shown_nm = re.sub(r"\D", "", labels.get("Артикул", ""))
    if shown_nm and shown_nm != str(requested_nm):
        return None, [f"wb_dom_card: requested nm {requested_nm}, page shows {shown_nm} — skipped"]

    raw_offers = payload.get("offers")
    all_lines, lines = lines, _without_offers_block(lines, raw_offers)
    other_offers = _parse_other_offers(raw_offers, requested_nm)

    # The buy block (the «Купить» / «В корзину» buttons) is the only place this card's own
    # offer — price, store, delivery — is read from. Without it the main offer is unavailable
    # (live 2026-10-01, nm 164379765): the prices and the store that remain on the page belong
    # to the other sellers, and must not be taken for this card's.
    buy_index = next((i for i, line in enumerate(lines) if line.lower() in _BUY_LINES), None)
    available = buy_index is not None
    regular = wallet = crossed = None
    delivery = warehouse = None
    store, store_rating = "", None
    if buy_index is not None:
        window = lines[max(0, buy_index - 8) : buy_index]
        prices = [p for line in window if "мес" not in line.lower() for p in prices_in(line)]
        regular, wallet, crossed = assign_prices(
            prices,
            wallet_hint=_first_price(payload.get("wallet"), prices),
            crossed_hint=_first_price(payload.get("del"), prices),
        )
        if regular is None and wallet is not None:
            warnings.append(f"wb_dom_card: nm {requested_nm} shows only a Wallet price; regular price unknown")
        tail = lines[buy_index:]
        delivery, warehouse, delivery_index = parse_delivery(tail)
        store, store_rating = _store_from_buy_block(tail, delivery_index)
        if not store:
            store, store_rating = _store_from_seller_texts(_str_lines(payload.get("seller")))

    returns = next((line for line in lines if line.lower().startswith("возврат")), None)
    address = labels.get("Адрес продавца", "")
    country_match = _COUNTRY_RE.match(address)
    country = country_match.group(1) if country_match else ("RU" if "россия" in address.lower() else None)
    aliexpress = any(line.strip().lower() == "aliexpress" for line in lines[:60])
    cross_border: bool | None = True if aliexpress or (country and country != "RU") else (False if country else None)

    brand = head_brand
    sku_index = next((i for i, line in enumerate(lines) if line.startswith("Артикул")), len(lines))
    head = lines[:sku_index]
    rating, feedbacks = parse_rating(head)
    if rating is None and feedbacks is None:
        rating, feedbacks = parse_rating(lines[:200])
    original_badge = True if any(line.strip() == "Оригинал" for line in head) else None
    offers_match = _OFFERS_RE.search("\n".join(all_lines[:200]))
    offers_count = int(offers_match.group(1)) if offers_match else None
    offers_from = float(re.sub(r"\D", "", offers_match.group(2))) if offers_match else None
    price = regular if regular is not None else wallet
    inn = labels.get("ИНН", "")
    item = {
        "nm_id": requested_nm,
        "name": name,
        "brand": brand,
        "supplier": store,
        "supplier_rating": store_rating,
        "review_rating": rating,
        "feedbacks": feedbacks,
        "total_quantity": None,
        "in_stock": bool(available and price is not None),
        "price_rub": price,
        "price_original_rub": crossed,
        "wallet_price_rub": wallet,
        "price_kind": "regular" if regular is not None else ("wallet" if wallet is not None else ""),
        "delivery": delivery,
        "warehouse": warehouse,
        "returns": returns,
        "cross_border": cross_border,
        "seller_legal_name": labels.get("Наименование продавца") or None,
        "seller_country": country,
        "seller_registration": labels.get("Номер регистрации") or labels.get("ОГРН") or labels.get("ОГРНИП") or None,
        "seller_inn": inn if inn and set(inn) != {"0"} else None,
        "warranty": labels.get("Гарантийный срок") or None,
        "original_badge": original_badge,
        "other_offers_count": offers_count,
        "other_offers_from_rub": offers_from,
        "other_offers": other_offers,
        "transport": "dom",
    }
    if not available:
        parts = [f"wb_dom_card: nm {requested_nm} main offer unavailable"]
        if offers_count is not None or other_offers:
            count = offers_count if offers_count is not None else len(other_offers)
            tail_text = f"{count} other sellers"
            if offers_from:
                tail_text += f" from {offers_from:.0f} ₽"
            cheapest = other_offers[0] if other_offers else None
            if cheapest and (not offers_from or cheapest["price_rub"] == offers_from):
                tail_text += f" (cheapest nm {cheapest['nm_id']})"
            parts.append(tail_text)
        warnings.append("; ".join(parts))
    elif price and offers_from and offers_from < price * OTHER_OFFERS_ANOMALY:
        warnings.append(
            f"wb_dom_card: nm {requested_nm}: other sellers from {offers_from:.0f} ₽ vs {price:.0f} ₽ here "
            f"({offers_from / price - 1:+.0%}) — anomalously cheap offers of the same card, treat as counterfeit risk"
        )
    return item, warnings


def search_url(query: str, page: int = 1) -> str:
    url = f"{SITE}/catalog/0/search.aspx?search={urllib.parse.quote(query.strip())}"
    return url if page <= 1 else f"{url}&page={page}"


def card_url(nm_id: int) -> str:
    return f"{SITE}/catalog/{int(nm_id)}/detail.aspx"


# ---------------------------------------------------------------------------
# Rendering through the operator's Chrome
# ---------------------------------------------------------------------------

_dom_cache: TTLCache[dict[str, Any]] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=128)
_dom_lock = asyncio.Lock()
_last_nav = 0.0


async def _gap() -> None:
    wait = _settings.dom_min_gap - (time.monotonic() - _last_nav)
    if wait > 0:
        await asyncio.sleep(wait)


def _stop_blocked(url: str, probe: dict[str, Any]) -> None:
    log_event("wb_dom.blocked", url=url[:120], captcha=bool(probe.get("captcha")))
    if probe.get("captcha"):
        raise_tool_error(
            ChallengeRequiredError(
                "WB is showing a captcha in the scraping-profile Chrome. Not retrying: the operator "
                "decides whether to open wildberries.ru there and continue.",
                provider="wb",
                challenge_type="captcha",
            )
        )
    raise_tool_error(
        TransportDownError(
            "WB shows «Подозрительная активность» in the scraping-profile Chrome. Not retrying: wait "
            "10-60 min before the next WB call; if it persists, clear wildberries.ru site data in that profile.",
            provider="wb",
            retry_after_s=600.0,
        )
    )


async def _wait_ready(page: Any, want: str, url: str) -> dict[str, Any]:
    """Poll until the page is readable; stop on a hard block. ``want``: search|card."""
    deadline = time.monotonic() + _settings.dom_wait_s
    last: dict[str, Any] = {}
    stable_tiles = -1
    card_ready_at: float | None = None
    store_seen_at: float | None = None
    while True:
        raw = await asyncio.wait_for(page.evaluate(PROBE_JS), timeout=15.0)
        probe = json.loads(raw) if isinstance(raw, str) else {}
        last = probe if isinstance(probe, dict) else {}
        state = last.get("state")
        if state == "blocked":
            _stop_blocked(url, last)
        if state == "ok":
            if want == "search":
                tiles = int(last.get("tiles") or 0)
                if last.get("empty") and tiles == 0:
                    return last
                if tiles > 0 and tiles == stable_tiles:
                    return last
                stable_tiles = tiles
            elif last.get("missing"):
                return last
            elif last.get("sku") and last.get("rub"):
                # Prices and «Артикул» are up; the store block trails them. Wait for it,
                # but never longer than dom_store_wait_s — some cards show no store rating.
                now = time.monotonic()
                card_ready_at = card_ready_at if card_ready_at is not None else now
                if last.get("store") and store_seen_at is None:
                    store_seen_at = now
                store_done = store_seen_at is not None or now - card_ready_at >= _settings.dom_store_wait_s
                if store_done:
                    # The other-sellers block trails the store; most cards never show it, so the
                    # extra wait is short and bounded.
                    offers_clock = (
                        store_seen_at if store_seen_at is not None else card_ready_at + _settings.dom_store_wait_s
                    )
                    if last.get("offers") or now - offers_clock >= _settings.dom_offers_wait_s:
                        return last
        if time.monotonic() >= deadline:
            if state == "challenge":
                raise_tool_error(
                    TransportDownError(
                        f"WB browser check did not clear within {_settings.dom_wait_s:.0f}s in the "
                        "scraping-profile Chrome — open wildberries.ru there once, then retry",
                        provider="wb",
                    )
                )
            return last
        await asyncio.sleep(0.75)


async def render(url: str, extract_js: str, want: str) -> dict[str, Any]:
    """Open ``url`` in the scraping-profile Chrome, wait, extract, close the tab."""
    global _last_nav
    cached = _dom_cache.get(url)
    if cached is not None:
        return cached
    try:
        from mcp_core.transport.chrome_cdp import NavBlocked, open_page
    except ImportError as exc:  # pragma: no cover - playwright ships with the monorepo venv
        raise_tool_error(
            TransportDownError(f"WB_TRANSPORT=dom needs the CDP transport (playwright): {exc}", provider="wb")
        )
    async with _dom_lock:
        await _gap()
        try:
            async with open_page(url, wait_ms=_settings.dom_settle_ms, allowed_hosts=ALLOWED_HOSTS) as page:
                probe = await _wait_ready(page, want, url)
                raw = await asyncio.wait_for(page.evaluate(extract_js), timeout=30.0)
        except NavBlocked as exc:
            log_event("wb_dom.nav_blocked", status=exc.status)
            raise_tool_error(
                TransportDownError(
                    f"WB refused the page (HTTP {exc.status}) in the scraping-profile Chrome",
                    provider="wb",
                    status_code=exc.status,
                )
            )
        except TimeoutError:
            raise_tool_error(TransportDownError("WB page did not answer the extractor in time", provider="wb"))
        finally:
            _last_nav = time.monotonic()
    if not isinstance(raw, str) or len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise_tool_error(ParserDriftError("WB page extractor returned no payload or an oversized one", provider="wb"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise_tool_error(
            ParserDriftError(f"WB page extractor returned invalid JSON: {_redact(str(exc))}", provider="wb")
        )
    if not isinstance(data, dict):
        raise_tool_error(ParserDriftError("WB page extractor returned a non-object payload", provider="wb"))
    # Only the page's own «ничего не нашлось» makes an empty grid a real empty search;
    # without it an empty grid is selector drift (see _wb_search_dom).
    data["page_says_empty"] = bool(probe.get("empty"))
    if _usable(data, want):
        _dom_cache.set(url, data)
    return data


def _usable(data: dict[str, Any], want: str) -> bool:
    """Cache only a render that carries what the parser needs; a half-rendered page must be re-read."""
    if want == "search":
        tiles = data.get("tiles")
        return isinstance(tiles, list) and bool(tiles)
    lines = data.get("lines")
    return isinstance(lines, list) and any(isinstance(x, str) and x.startswith("Артикул") for x in lines)
