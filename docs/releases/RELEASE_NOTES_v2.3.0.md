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

## Evidence and limits

- CI covers Windows, Linux, and macOS with Python 3.12/3.13.
- Native vision was verified against a live local Chrome/MCP fixture: the model
  read a generated value from a scrolled JPEG viewport, with dimensions bounded
  to 1440×900.
- No CAPTCHA solver is included. Early HTTP blocks and hard-process-kill expiry
  remain outside the handoff contract. MPN/GTIN stays `unknown` when a source
  does not provide manufacturer identifiers.

Contributors are credited in `CONTRIBUTORS.md`; this release contains maintainer
work and the continued contributions already listed there.

