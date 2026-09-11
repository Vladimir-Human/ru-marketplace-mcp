# v2.2.0

Minor release: the Cian real-estate connector, operator-chosen source mounting,
and a verification-driven hardening pass (release-gate parity, tri-state
honesty for Taobao, Megamarket privacy).

## Added

- **Cian connector** (`cian-connector`, `cian-mcp`, tools `cian_search` /
  `cian_card`): Russian real estate — flats, rooms, houses and commercial
  property for sale, long-term rent or daily rent. Tier 2 only (Cian's WAF
  blocks plain HTTP by IP; reads run inside the operator's Chrome over CDP,
  JSON both ways, no HTML parsing). Search is by filters, not text. Every row
  carries `price_unit` (`total`/`month`/`day`) because a nightly 5 000 ₽ must
  never rank against a monthly 90 000 ₽; a missing price is `None`, never 0.
  Cian deliberately takes no part in `compare_prices`. (#47)
- **`MARKETPLACE_SOURCES`**: the unified mount now installs only the sources
  the operator names (canonical names plus `wb`/`ym`/`detmir`/`ali` aliases;
  unset = everything, pinned tool counts unchanged). Every mounted tool schema
  is paid on every request, so a seven-source operator stops paying for six
  (measured 17 086 → 11 851 wire tokens). Deselected sources stay visible in
  `marketplace_sources` → `skipped` with reason `deselected`, and
  `compare_prices` queries the same selection. Unknown names are rejected at
  startup — a typo cannot silently create a partial server. (#48)
- **Dependency-parity gate**
  (`packages/marketplace-connector/tests/test_dependency_parity.py`): every
  source in `_mount_all` must be a declared dependency and a workspace source
  of `marketplace-connector`, and vice versa. The "registered in one place,
  forgotten in another" bug class is now a gate, not a reviewer's memory. (#49)
- Taobao login-wall regression fixture: a real trimmed capture of the
  empty-title wall with provenance and a session-data-absence test. (#51)

## Changed

- **Megamarket privacy (finding S4)**: reading the profile address list
  (`/profileService/address/list`, an account-gated endpoint fetched with the
  operator's cookies) is now opt-in via `MEGAMARKET_USE_PROFILE_ADDRESS=1`,
  default off. The public suggest fallback keeps search working unchanged.
  The address source is disclosed in `_meta` warnings (only for states that
  change what prices mean — a clean default search stays `healthy: true`),
  and the raw `addressId` never leaves the process, including error paths.
  `SECURITY.md` (RU+EN) corrected: MPStats is no longer described as the only
  account-gated surface. (#52)
- Documented counts moved: 1360 offline tests, 16 workspace members, 38 tools
  across 14 source servers (39 on the unified mount), 15 agent skills, 84
  version declarations, 32 release artifacts.
- `scripts/test_ops_gates.py` gained a `__main__` runner: the documented
  invocation used to exit 0 without running its three tests — a gate that
  cannot fail checks nothing. (#50)

## Fixed

- **Yandex search price semantics** (#53, found by this release's live
  verification): `yandex_search` quoted the SERP strike-through
  `initialPrice` as `price_rub` (+61 % on the measured item) and fed it to
  `compare_prices` rankings; it now quotes the SERP cart price and can no
  longer reach `initialPrice`. Fixture provenance that had pinned the buggy
  parser's output was corrected from the same captures. Details in the live
  table below.
- `marketplace-connector` now declares `aliexpress-connector` as a hard
  dependency; a standalone wheel no longer silently drops AliExpress. (#49)
- `marketplace_sources` capabilities: the `mounted` flag was always `false`
  for Yandex Market and Detsky Mir (mount table says `yandex`/`detmir`,
  metadata says `yandex_market`/`detsky_mir`). (#48)
- **Taobao tri-state honesty** (#51): a login wall with an empty `<title>`
  (the shape observed live 2026-09-10) used to reach `taobao_selfcheck` as
  `drift_detected`, making `doctor` exit 1 — a release no-go — for a plain
  logged-out session. Wall detection now reads structural markers (login
  routes, anchor counts, visible body text), decided in Python; the captcha
  verdict left the extractor JS (hidden baxia widget text «人机» convicted
  healthy pages rendering 29–38 items, so the shape-drift canary never ran)
  and is gated on a zero-item extraction; a card whose product *name*
  contains «登录» is no longer mistaken for a wall.
- The docker release gate (`e2e_stdio_check_docker.py`) demanded 13 mounted
  sources after Cian made 14 — the next tag build would have died before
  publishing. (#47 review fix)

## Verification

- Offline suite green on Ubuntu, Windows and macOS × Python 3.12/3.13 (CI);
  locally 1359 passed + 1 pre-existing conditional skip; ruff, mypy
  (host/win32/darwin), no-print, versions (2.2.0 × 84), test-count (1360 × 7),
  coverage ≥ 70 %, `e2e_stdio_check.py` 16/16 real MCP sessions at 2.2.0.
- Each change passed an independent multi-area review with empirically
  reproduced findings before merge; two majors caught in review (a raw
  address id leaking through an error message; `_meta.healthy` inverted by an
  always-on disclosure) were fixed and re-verified.

### Live source status at release time (operator machine, residential RU IP,
### Chrome CDP session; `marketplace-mcp doctor` exit 2 — conditional go,
### 7 healthy / 5 inconclusive / 0 drifted, 2026-09-11 10:24–10:52 MSK)

| Source | Doctor | Eye-comparison (2 items: connector vs the live site) |
|---|---|---|
| Wildberries | healthy | 2/2 MATCH (price/strike-through/rating/stock/supplier; via CDP render — plain httpx gets HTTP 498) |
| Yandex Market | healthy | card 2/2 MATCH (all three prices + seller + stock); search: a price-semantics discrepancy was FOUND by this verification, fixed in #53 before tag, and re-verified live (details below) |
| Detsky Mir | healthy | 2/2 MATCH (JSON-LD price/availability, old price, marketplace seller, store counts) |
| Avito | healthy | 2/2 MATCH (price, posting time to the minute UTC↔MSK, seller entity) |
| Megamarket | healthy | 2/2 MATCH (price/old price/availability; first release with the profile opt-in default OFF — address resolved via public suggest) |
| Cian | healthy | 2/2 MATCH (total price, agency, area/address) |
| Taobao | healthy (search) | search verified live (39 positions, CNY-only as designed); item-level card verification UNVERIFIABLE — cards redirect to login.taobao.com in the logged-out scraping profile. The empty-title login wall that previously mis-triaged as `drift_detected` is now correctly `inconclusive(login_wall)` (#51 effect observed live) |
| Ozon | inconclusive | HTTP 403 on all four endpoints from this network — not verified |
| Lamoda / DNS / Citilink | inconclusive | transport_down / challenge from this network — not verified |
| MPStats | inconclusive | `auth_missing` (no token configured) — not verified |

**Yandex search price semantics — found by this verification, fixed in this
release (#53).** The verification caught `yandex_search` reporting the SERP
snippet's strike-through `initialPrice` (3698 ₽) as `price_rub` for an item
whose actual non-subscription price was 2293 ₽ at the time (`yandex_card`
for the same item was always correct). Root cause: the SERP state's
`offer.price.value` carries the strike-through price on discounted rows.
Fix: `price_rub` now comes from the SERP cart price
(`productPayload.cartButton.price.valueFmt`), then
`additionalPrices[withDiscount]`; `initialPrice` can no longer reach
`price_rub`. Live post-fix re-check: search Tuvio → 2367 ₽ == card 2367 ₽
(the intraday move from 2293 proves search==card semantics, not a stale
pin). The July fixtures' provenance turned out to record the buggy
parser's output as "displayed" prices — values were re-read from the same
captures (sha256 unchanged) and the notes corrected with an explicit
annotation. A second, site-side quirk is documented (not fixed, it is
upstream): a SERP snippet can carry a different family offer (KM243 @2559)
than the product card's default (KM245 @4146) — the connector is
SERP-faithful; search rows verify against `sku_id`, not the product URL.

**Cross-marketplace check** (`compare_with_china "iphone 15"`, 2 runs):
`complete: false` reported honestly, cheapest picked among rouble sources,
**zero CNY rows in the ranking** — empirically and structurally
(`_search_taobao` sets `price_rub=None`; the ranking filter requires
`currency == 'rub'`), so yuan cannot win a rouble ranking even with Taobao
alive (control run: 39 live Taobao positions, CNY-only).

**Known observation (follow-up, not a regression):** during `compare_prices`'
parallel fan-out across six CDP sources, all CDP navigations can abort
simultaneously (`ERR_ABORTED`/`TargetClosedError`) from Chrome tab
contention while isolated calls to the same sources stay healthy. The
degradation is honest (per-source `blocked`, `complete: false`), but
comparison completeness suffers; a fan-out pacing/tab-budget fix is queued.

Unverified sources are labelled as such everywhere; none is claimed working.

Known follow-ups (not blocking): lamoda carries the same JS-baked
`__BLOCKED__` pattern that #51 removed from taobao; the Megamarket address
cache is per-process rather than per-profile (S4 second stage); model-level
routing evals and stored wire baselines remain on the roadmap
(`work/v2-research/`).
