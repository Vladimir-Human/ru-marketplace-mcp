# ru-marketplace-mcp v2.3.0

This release makes browser challenges recoverable and image-aware while making
card verification stricter about which offer is being checked.

## Highlights

- `compare_browser_snapshot(handoff_id)` returns a bounded JPEG viewport through
  standard MCP image content for native-vision clients. It is session-scoped,
  lease-bounded, and does not OCR, solve CAPTCHA, or call another model.
- Lamoda and Taobao challenge handoffs retain the exact browser page and resume
  without a new navigation. DSH profile selection is mutually exclusive:
  full, then decision, then compare.
- WB verification preserves unambiguous typed color evidence. Verification reads
  the requested WB row, unwraps Detsky Mir cards, and uses Ozon's regular price.
- Yandex verification can require the search-row `sku_id`, preventing a card for
  a different family offer from being presented as a live price delta.
- Stored wire baselines now cover compare, decision, and unified profiles.
- HTTP containers now require `MCP_HTTP_AUTH_TOKEN` and `MCP_HTTP_TENANT_ID` for
  their non-loopback bind; Compose passes these through mandatory substitutions.

## Evidence and limits

- CI covers Windows, Linux, and macOS with Python 3.12/3.13.
- Native vision was verified against a live local Chrome/MCP fixture: the model
  read a generated value from a scrolled JPEG viewport, with dimensions bounded
  to 1440×900.
- No CAPTCHA solver is included. Early HTTP blocks and hard-process-kill expiry
  remain outside the handoff contract. MPN/GTIN stays `unknown` when a source
  does not provide manufacturer identifiers.

## Live source status at tag time (conditional go, `doctor` exit 2)

Operator machine, residential RU IP, Chrome CDP session; probes of
2026-09-13 (morning doctor run + 13:15–13:22 MSK targeted live/CDP probes with
saved artifacts and sha256).

- **Yandex Market — degraded upstream, honestly classified.** Around
  Sep 12–13 Yandex moved search/card product data from the SSR state into
  client-side lazy loading: anonymous HTTP now receives a hollow product
  frame (`pageId market:product`, all product collections empty) and search
  survives only via the schema.org ld+json fallback; a real browser hits
  SmartCaptcha on product pages. The familiar field families did not change
  shape — they are absent — so this release classifies the state as
  `empty_product_shell` / `ok_ldjson_only` → `inconclusive`, never
  `parser_drift` (a false drift alarm would have made `doctor` exit 1 and
  blocked this release for a serving change). Yandex card data is **not
  verified** in this release; search rows come from the ld+json fallback
  whose schema.org price is the Plus-subscription price, so degraded rows
  carry `price_with_plus` with `price_rub: null` and never win a ranking.
  Recovery path (first-screen `data-zone-data` blobs) is queued for v2.4.
- Sources answering `inconclusive` at tag time keep their specific reasons
  in `doctor` output (Ozon HTTP 403 from this network; Lamoda/DNS/Citilink
  challenge/transport; MPStats `auth_missing`); none is claimed working.
- `compare_with_china` guarantee stands: CNY offers never enter rouble
  rankings (structural filter, unit-tested).
- Note for operators: the scraping Chrome profile received a SmartCaptcha on
  market.yandex.ru product pages during release verification — card-level
  CDP checks for Yandex currently require passing that check by hand once.

Contributors are credited in `CONTRIBUTORS.md`; this release contains maintainer
work and the continued contributions already listed there.
