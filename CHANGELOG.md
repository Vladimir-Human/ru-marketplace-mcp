# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-26

First public release. The project grew from two connectors into a uv workspace of
five MCP servers over a shared runtime.

### Added

**New marketplaces**
- **Yandex Market** connector (`yandex_search`, `yandex_card`, `yandex_selfcheck`).
  Reads the server-rendered widget state, since Yandex exposes no usable JSON API.
  Reports the everyday price and the Plus-subscriber price separately, plus the
  per-star rating distribution and server-rendered reviews.
- **Detsky Mir** connector (`detmir_card`, `detmir_category`, `detmir_categories`,
  `detmir_selfcheck`) over its anonymous public JSON API, including offline store
  availability.

**Cross-marketplace comparison**
- New `compare-connector` with `compare_prices` and `compare_sources`. Queries
  every installed marketplace concurrently, ranks offers by everyday price, and
  reports a per-source outcome so a partial result is never mistaken for a
  complete one. Subscription-only prices are excluded from ranking.

**New Wildberries tools**
- `wb_seller(supplier_id)` — registered legal entity, INN, KPP, OGRN, legal
  address and trademark behind a seller id.
- `wb_categories(root, max_depth)` — catalog tree with WB's own shard/query
  selectors, bounded so a response stays a usable size.

**Shared runtime (`mcp-core`)**
- `transport.http_tier` — polite rate limiting, capped bodies, and retries scoped
  to transport faults and gateway statuses (429 deliberately excluded).
- `transport.chrome_cdp` — the authenticated tier, generalised out of the Ozon
  connector and now cross-platform.
- `process` — cross-platform worker spawn/reap with an allowlisted child
  environment.
- `cache` — in-process TTL cache with concurrent-miss collapsing.
- Proxy support across connectors via `*_PROXY` or the standard proxy variables.

**Project infrastructure**
- uv workspace monorepo; each connector is an installable package with a console
  script (`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`).
- GitHub Actions CI: ruff, mypy and the test suite on Ubuntu/Windows/macOS against
  Python 3.12 and 3.13.
- `scripts/check_no_print.py` — fails the build on any stdout write in server
  code, since a stray `print()` corrupts the JSON-RPC stream.
- `scripts/start_chrome_cdp.sh` — Linux/macOS counterpart to the PowerShell
  launcher.
- Agent skill documentation for every connector.
- Test suite grown from 66 to 221 offline tests, including real trimmed fixtures
  for the Yandex SSR parser.

### Fixed

- **`wb_search` returned pages where nothing had a price.** It resolved ids through
  `search-goods.wildberries.ru`, which serves a stale index: for one live query
  every id it returned was a delisted SKU with `price: null`, while the v9 search
  endpoint returned 100 in-stock products with real prices. `wb_search` now reads
  `search.wb.ru` v9 directly — one request instead of two, 100 results per page
  instead of 30 — and keeps the old path as a flagged fallback.
- **Ozon's process teardown was Windows-only.** `taskkill` paths, creation flags
  and the child environment allowlist assumed Windows; the POSIX branch was
  untested and its test asserted a Windows path, so it could not pass on Linux or
  macOS. Now cross-platform, with both branches unit-tested on every OS.
- **`taskkill` could be redirected through the environment.** The system directory
  was resolved via `SystemRoot`/`WINDIR`, which any process able to set the
  environment could point elsewhere. Now resolved via `GetSystemDirectoryW` or a
  literal fallback.
- **Windows paths were built with forward slashes off-Windows.** Switched to
  `PureWindowsPath` so the Windows branch composes correct paths when exercised
  from a POSIX host.
- **POSIX-only calls broke type checking and tests on Windows.** `terminate_process_tree`
  referenced `os.killpg`, `os.getpgid` and `signal.SIGKILL` literally. Those names do
  not exist on Windows, so mypy failed there while passing on Linux, and the POSIX
  tests could not monkeypatch attributes the module lacked. The calls now go through
  `kill_process_group()`, which resolves them via `getattr` and raises cleanly where
  process groups are unavailable; the tests patch that function instead. CI now runs
  `mypy --platform win32` and `--platform darwin`, which is what would have caught this
  from a Linux host in the first place.
- **PEP 561 markers were missing.** Without `py.typed`, mypy treated every
  cross-package import as `Any` and reported phantom missing-return errors. All
  packages now ship the marker; the tree is mypy-clean.
- **Error bodies were truncated unconditionally.** Detsky Mir's search route
  answers 404 while rendering a full page, so an error-body cap discarded real
  content. The cap is now opt-out per call.
- **Gateway errors were not retried.** Detsky Mir emits sporadic 502s and Yandex
  occasionally answers 302 with an empty body; both are now retried, while 429 is
  still passed straight through.

### Removed

- **`detmir_search` was implemented, tested against live data, and deleted.** Its
  results were plausible-looking nonsense: a query for "лего" returned nappies and
  collagen supplements, because Detsky Mir's API ignores text filters and its
  website search route renders a promo carousel behind a 404. No search tool is
  better than a confidently wrong one; discovery goes through `detmir_categories`.

### Not included, and why

Marketplaces evaluated during this release and deliberately left out:

- **Megamarket** — its mobile API works, but ServicePipe blocks datacenter traffic
  outright and requires cookies from a browser that has passed a JS challenge.
- **Lamoda** — its GraphQL endpoint returns prices for a *known* SKU, but catalog
  and search sit behind an anti-bot redirect loop, so there is no way to discover
  products in the first place.
- **DNS** — Qrator serves a JavaScript proof-of-work challenge on all dynamic
  pages; only `robots.txt` and `sitemap.xml` are reachable anonymously.
- **Citilink** — Qrator rate-blocks the entire domain, and the data transport is
  gRPC-web requiring a reversed protobuf schema.

Details in [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

[1.0.0]: https://github.com/Vladimir-Human/ru-marketplace-mcp/releases/tag/v1.0.0
