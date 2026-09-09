# Deep Research: ru-marketplace-mcp 2.0.0

> Generated 2026-09-09 | Depth: deep | Research streams: 6 | Status: implementation plan plus first-wave fixes

## Decision summary

Version 2.0.0 should be a trust and decision-quality release, not another
connector-count release. The repository already has broad source coverage and
strong offline parser discipline. The largest remaining risks sit at the
boundary where an agent turns heterogeneous marketplace observations into a
purchase recommendation: product identity is only loosely matched, price
conditions are easy to conflate, source readiness is partly implicit, and the
full DSH mount has a large fixed context cost.

The first wave implemented in this checkout is deliberately measurable:

- `compare_prices` exposes raw `cheapest`, safe `cheapest_comparable`, and an
  `in_stock_only` purchase filter;
- `compare_verify_offer` performs a cheap source-native card verification after
  comparison, without activating the 36-tool unified mount;
- `marketplace_sources.capabilities` exposes static access, currency, login/CDP,
  search, and mounted-state metadata before a network call;
- DSH skills route ordinary price questions to the cheap compare mount and
  explain full-mode activation;
- MPStats upstream inner errors are redacted both in logs and in MCP ToolError;
- regression tests, public contract snapshots, and cross-platform CI gates own
  these behaviors.

Live verification added an important concrete case: for Yandex product id
`6203146574`, search returned 21,783 ₽ for one seller row, while the manually
verified browser card showed the same product family at 18,565 ₽ (18,194 ₽ Pay)
from RBT.ru. The new `expected_price_rub` argument on `compare_verify_offer`
reports this delta explicitly instead of pretending the search row is current.
The model is therefore allowed to say “search result stale/different offer”,
which is materially safer than silently choosing a number.

The second wave is required before calling the full 2.0 architecture complete:
exact product identity using model/GTIN/MPN evidence, profile-based DSH tool
presets, stored drift baselines, machine-readable performance gates, HTTP/CDP
security hardening, and per-source SLOs.

## Research method and evidence limits

Six streams were requested: product capabilities, DSH routing/cost, connector
reliability, MCP ecosystem, security/privacy, and evaluation/operations. Direct
official sources were retrieved over HTTP where the configured A6 search route
returned `auth_missing`; no unauthenticated search snippet was promoted to a
fact. Several exploratory agents were rate-limited (429), so the report marks
repository-derived claims separately from external-source claims.

The repository is the authority for current behavior. Runtime evidence includes
the actual MCP wire probe, offline suite, contract snapshot, DSH parity tests,
and GitHub CI. Live marketplace observations remain environment-specific and
must not rewrite golden fixtures automatically.

## Finding 1 — Comparability is the product boundary [High]

`compare_prices` already knows that accessories, refurbished goods, display
units, foreign currencies, subscription prices, and partial source failures can
make a numeric minimum misleading. Before the first wave, those signals were
warnings while `cheapest` remained the only positive winner field. A model had
to manually reinterpret the answer. The new `cheapest_comparable` makes that
interpretation explicit while preserving the raw row for audit.

This is still not exact identity resolution. Marketplace titles can omit model
numbers, merge variants, or contain seller marketing. Product identity must be
promoted to a separate evidence object in the next wave, with source-native IDs,
model/GTIN/MPN tokens, variant attributes, and an explicit unknown state. Never
turn fuzzy title overlap into a claim that two rows are the same SKU.

## Finding 2 — Availability and price conditions must be first-class [High]

The most useful “where can I buy it?” answer is not the cheapest historical or
unavailable listing. `in_stock_only=true` now restricts the winner and spread to
offers whose source explicitly reports stock while retaining excluded rows and
warnings. This preserves provenance and prevents silent data loss.

The same model should grow to represent delivery region, shipping cost, coupon
eligibility, subscription requirements, and seller condition. Each condition
needs a tri-state (`true`, `false`, `unknown`) or an explicit evidence object;
absence cannot be converted into a positive claim.

## Finding 3 — DSH needs progressive disclosure [High]

The local wire measurement shows the cheap compare mount at approximately 0.9k
tokens/request and the unified mount at approximately 13.8k. Nearly half of
full-mount cost is tool descriptions. The expensive surface should be activated
only after a task needs it. MCP’s tool contract makes discovery, typed schemas,
descriptions, and structured results part of client behavior [P1].

The first wave removes overlapping price triggers and documents the full-mode
environment gate. The next wave should ship three explicit profiles:

1. **compare** — search, stock-aware ranking, source outcomes, card verification;
2. **decision** — compare plus selected cards/reviews for a winner;
3. **full** — all sources and operator workflows.

Each profile needs a real wire budget and a routing eval, not only a YAML label.

## Finding 4 — Source readiness needs preflight and provenance [High]

Static capabilities and live doctor answer different questions. Capabilities say
what a source requires; doctor says whether this operator session currently works.
The unified response now exposes the former. The next layer should add a cheap
readiness summary with timestamp, route, auth/CDP posture, and a bounded reason,
while retaining selfchecks as CLI-only probes.

Every offer should carry provenance sufficient to answer: which connector,
which route, which request time, which source-native ID, and which price/stock
evidence. This is more valuable than a generic confidence score because an agent
can explain and re-check the result.

## Finding 5 — Drift and operations need binary gates [High]

The current `mcp_wire.py` reports latency and schema cost but exits successfully
even when a budget regresses. `diagnose_drift.py` has useful classifications, but
stored shape baselines are not wired into every selfcheck. CI tests offline and
three live canary sources; it cannot claim all live sources are healthy.

The v2 matrix should keep critical dimensions binary and thresholds separate:

| Dimension | Required v2 gate |
|---|---|
| Product correctness | 100% critical fields; zero fabricated price/currency/stock; preserve valid records in mixed payloads |
| Routing | deterministic 200/429/5xx/timeout/challenge/JSON-block cases classified correctly |
| Drift | synthetic mutations label success, drift, or inconclusive with no wall/geo false positive |
| Partial failure | valid records survive one malformed item; source outcome and warning are present |
| Cost/latency | p95 and max recorded; wire/schema regression budget enforced in CI |
| Doctor | every source state, exit precedence, status-file schema, and redaction covered |
| Security | no secrets in logs or ToolErrors; auth/tenant/CDP boundaries tested |

Do not average these into one score: a great median cannot compensate for a
single secret leak or a false cheapest recommendation.

## Finding 6 — Security boundaries are real [High]

The security stream confirmed an MPStats error-redaction bypass: an upstream
inner error was redacted in stderr but returned verbatim in the MCP ToolError.
The first wave fixes this and adds a synthetic secret regression test.

The same stream identified high-impact v2 work: routable HTTP has no built-in
authentication or tenant boundary; CDP navigation does not enforce final-host
allowlisting after redirects; Megamarket reads a profile address despite the
documented private-data model; and the raw CDP websocket uses an unbounded frame
size. These findings need separate threat-model and regression work before a
public HTTP/full-browser deployment can be called hardened.

External references support these boundaries: MCP transport guidance recommends
localhost binding and authentication [P2]; OWASP SSRF guidance treats internal
network targets as a distinct boundary [P3]; OWASP GenAI guidance recommends
separating external content and enforcing least privilege [P4].

## Findings from external research

Product identity sources converge on a layered model: Product/ProductGroup and
Offer concepts distinguish a product family, variant, and seller offer [P5];
Google’s product data guidance treats identifiers, condition, availability,
price, shipping, and returns as separate commerce facts [P6]; GTIN/MPN evidence
is stronger than title similarity, while missing identifiers must remain unknown
[P7]. The WDC Products benchmark demonstrates that product matching is a
separate entity-resolution task with measurable precision/recall trade-offs
[P8].

Operational sources support traces around browser actions and network events,
OpenTelemetry correlation, and SLO/error-budget thinking rather than logs alone
[P9][P10][P11]. MCP official sources cover tools, resources, prompts, progress,
and HTTP authorization; FastMCP’s migration guide confirms that a major runtime
upgrade must be treated as a contract migration, not a dependency bump [P1][P12].

## v2.0 work packages

### WP1 — Evidence model

Add typed `OfferEvidence` and `ProductIdentity` structures. Preserve source,
route, observed-at, native ID, model/GTIN/MPN tokens, condition, availability,
currency, and untrusted-text provenance. Keep backward-compatible top-level
fields during one deprecation cycle.

### WP2 — Identity and decision engine

Implement deterministic identifier matching first, normalized model-token matching
second, and an explicit `unknown` result when evidence is insufficient. Add a
golden dataset with positive/negative/near-miss variants and measure precision,
recall, and false-cheapest rate.

### WP3 — Profiled DSH surface

Ship compare/decision/full profiles with explicit tool sets and wire budgets.
Add positive and negative trigger fixtures so “где дешевле” cannot select the
full profile while “открой все источники” cannot select compare-only.

### WP4 — Runtime reliability

Wire stored shape baselines into selfchecks, make `mcp_wire` fail on configured
regressions, add per-source p50/p95/max telemetry, and emit machine-readable
doctor artifacts containing commit/version/route/timing/verdict.

### WP5 — Security hardening

Fix all upstream-derived error boundaries, require auth or fail closed for
non-loopback HTTP, enforce final CDP host policy, scope Megamarket private data,
and bound raw CDP frames. Add synthetic secret, redirect, tenant-isolation, and
prompt-injection fixtures.

### WP6 — Delivery and migration

Keep FastMCP major upgrades behind contract snapshots. Publish profile-specific
wire measurements, update DSH runtime pins deliberately, and make the release
workflow validate package names, versions, manifests, profile counts, and OCI
identifiers together.

## Open questions

- Which delivery regions and shipping totals should be part of the first identity
  model, given that several sources report only item price?
- Should `decision` profile cards be limited to the raw winner, the comparable
  winner, or both when those differ?
- Can the host expose a trustworthy DSH model-routing eval without coupling the
  repository to one private DSH runtime version?
- Which CDP redirect policy preserves marketplace login flows while rejecting
  off-host/private targets?

## Bibliography

[P1] Model Context Protocol and FastMCP official sources — tools, resources, prompts, progress, authorization, migration — protocol/official docs captured in `work/v2-research/protocol-sources.md`.
[P2] Model Context Protocol — transports/security — https://modelcontextprotocol.io/specification/2025-06-18/basic/transports — accessed 2026-09-09.
[P3] OWASP — SSRF — https://owasp.org/www-community/attacks/Server_Side_Request_Forgery — accessed 2026-09-09.
[P4] OWASP GenAI — LLM01 Prompt Injection — https://genai.owasp.org/llmrisk/llm01-prompt-injection/ — accessed 2026-09-09.
[P5] Schema.org — Product/ProductGroup/Offer — https://schema.org/Product — accessed 2026-09-09.
[P6] Google Merchant Center — product data specification — https://support.google.com/merchants/answer/7052112 — accessed 2026-09-09.
[P7] GS1 — GTIN standards — direct page unavailable in this run; secondary official excerpts recorded in `work/v2-research/identity-sources.md`.
[P8] WDC Products — product matching benchmark — https://webdatacommons.org/structureddata/ — accessed 2026-09-09.
[P9] OpenTelemetry — traces — https://opentelemetry.io/docs/concepts/signals/traces/ — accessed 2026-09-09.
[P10] Playwright — trace viewer/actionability — https://playwright.dev/docs/trace-viewer and https://playwright.dev/docs/actionability — accessed 2026-09-09.
[P11] Google SRE — service level objectives — https://sre.google/sre-book/service-level-objectives/ — accessed 2026-09-09.
[P12] FastMCP official migration documentation — captured in `work/v2-research/protocol-sources.md`.

## Research artifacts

- `work/v2-research/identity-sources.md`
- `work/v2-research/protocol-sources.md`
- `work/v2-research/operations-sources.md`
- `work/v2-research/security.md`
- `work/v2-research` also contains the eval matrix returned by the read-only
  eval-design stream; the repository tests remain the executable source of truth.
