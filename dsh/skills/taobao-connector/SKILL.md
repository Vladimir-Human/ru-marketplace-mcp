---
name: taobao-connector
description: Use this skill when the operator needs Taobao marketplace data — search Chinese listings or read an item card. Trigger on Chinese or English queries like "taobao", "淘宝", "найди на таобао", "цена taobao". Always needs the operator's Chrome over CDP (signed mtop API). Skip for non-Taobao tasks.
---

# Taobao Connector

Reads Taobao inside the operator's own Chrome over CDP. The search page is a
client-side React app whose data layer is the signed mtop API — every XHR wants
a `sign` computed from the `_m_h5_tk` cookie, and unsigned probes answer
`FAIL_SYS_TOKEN_EMPTY`. There is no anonymous tier; the site's own JS signs
requests natively in a real browser.

## When to use
- Search Taobao by Chinese or English text
- One item's price, shop, sales label, description images

## Tools available
- `taobao_search(query, page)` — listings. Prices are in YUAN (CNY), never
  converted: price_cny is None when hidden — never 0.
- `taobao_card(item_id_or_url)` — one item card. item ids are 9-13 digit
  strings; pass a bare id or an item.taobao.com URL.
- `taobao_selfcheck()` — tri-state canary; renders one live search page.

## Gotchas
- Prices stay in yuan. Comparing against ruble sources needs an explicit rate;
  a baked-in one would go silently stale.
- A login wall (title 登录) means the scraping profile is logged out of
  taobao.com — log in there, then retry.
- The operator's Chrome must be running with CDP (scripts/start_chrome_cdp.sh).
