# Deep Research: Product and DSH Evolution of ru-marketplace-mcp

> Generated 2026-09-09 | Depth: standard | Sources: 4

## TL;DR

The project already has unusually strong transport and parser discipline. Its
main weakness is now the decision layer around those connectors: an agent can
receive a technically valid cheapest listing that is an accessory, a used
condition, or a partial result, and the DSH skill router can select the
expensive 36-tool mount for an ordinary price question. The first improvement
therefore belongs at the product boundary, not in another connector-specific
scraper.

This run implements three pieces of that boundary: `compare_prices` now exposes
`cheapest_comparable` alongside the raw `cheapest`; `marketplace_sources` now
returns static routing capabilities before a network request; and DSH skill
routing clearly sends price questions to the cheap compare mount while marking
full-only tools as opt-in. The remaining high-value work is a middle-sized DSH
tool preset and model-level routing evaluations.

## Executive Summary

The repository is a multi-package MCP workspace with 14 source-facing servers,
a unified mount, a comparison connector, a CLI doctor, and a DSH bundle. The
architecture deliberately preserves partial failures and provenance. This is
the right foundation for marketplace data, where anti-bot blocks, currency
differences, stale search indexes, and variant identity make a single numeric
answer unsafe.

The product surface still placed too much responsibility on the model. Before
this change, `cheapest` was the only positive recommendation field. The code
already emitted warnings for accessory titles, refurbished/display conditions,
and large price outliers, but a caller had to interpret those warnings and
override the headline itself. The new `cheapest_comparable` field makes the
safe recommendation explicit while preserving the raw offer for auditability.
This is intentionally conservative: the heuristic does not delete or hide
offers, and it does not claim exact product identity.

The second boundary problem was capability discovery. `marketplace_sources`
reported mounted and skipped imports, but not whether a mounted source required
Chrome, login, a paid token, text search, or a foreign currency. The new
capabilities map lets an agent choose a route before spending a blocked request.
It is static metadata, not a live health claim; `doctor` remains the authority
for runtime readiness.

The DSH audit found a cost and routing conflict. `compare-mcp` exposes two tools
at roughly 0.9k wire tokens per request, while `marketplace-mcp` exposes 36 tools
at roughly 13.8k. The marketplace and compare skills shared price-query
triggers, so a normal “где дешевле” request could select the expensive mount.
The full-only skills also described tools unavailable in the default DSH mode.
The routing descriptions now separate those modes and carry an explicit
`RU_MARKETPLACE_MCP_FULL=1` activation contract.

## 1. Status Quo [Confidence: High]

The codebase uses a shared `mcp-core` runtime for error taxonomy, transports,
coercion, pacing, caching, and output schema compaction. Connectors are
defensively imported and source outcomes are retained in comparison responses.
The project’s own architecture document states the core invariant: missing data
is `None`, never `0`, because zero would rank a dead listing as the cheapest
offer [3]. The current repository contains fixture-backed DOM tests, shape
references, live/CDP markers, a cross-platform matrix, and an MCP wire probe
[2][3].

The comparison tool fans out concurrently, ranks only ruble offers, keeps
foreign-currency offers visible, reports per-source outcomes, and warns about
accessories, conditions, and outliers. That is a strong base, but its previous
headline field did not distinguish “raw lowest row” from “safe comparable row”.
The new field closes that semantic gap without changing the raw ranking contract.

## 2. Emerging Product Direction [Confidence: Medium]

The MCP tools specification treats tools as discoverable capabilities with
typed input/output contracts and human-readable descriptions [1]. That makes
tool routing and schema cost product concerns, rather than implementation
details. In this repository, the wire probe shows that descriptions are a major
part of the full mount’s context cost [4]. The practical direction is therefore
progressive disclosure: expose a cheap decision tool first, then activate a
source-specific or full tool group only when the task needs it.

For marketplace agents, the next useful abstraction is not another generic
search result. It is a decision record with explicit comparability, source
coverage, currency, access state, and follow-up identity. The implementation
now has the first two pieces. A future `comparison_profile` or source-scoped
preset should add the third without making every request pay the full schema.

## 3. Critical Assessment [Confidence: Medium]

The comparable-price heuristic is not identity resolution. Titles can omit a
model number, merge variants, or contain seller marketing. `cheapest_comparable`
should therefore be phrased as a safer candidate, not as proof that all rows are
the same SKU. The tool documentation continues to tell the agent to verify the
winning card, seller, stock, and exact model.

The capability map is not health telemetry. A source can be marked
`anonymous_http` and still return a live block; a CDP source can be healthy in
one profile and unavailable in another. Keeping this distinction explicit is
important: static routing metadata prevents predictable misuse, while doctor
and source outcomes provide runtime evidence.

The DSH activation note reduces tool-not-found errors, but all skills are still
catalogued in the default profile. A stronger design would install a cheap
compare skill set by default and opt into the full connector skill set, or add a
middle source-scoped preset. That change needs an integration test against the
actual DSH runtime rather than a regex-only skill parity check.

## 4. Implemented Action Plan

- [x] Expose `cheapest_comparable` while retaining raw `cheapest` and warnings.
- [x] Add static access/currency/search capabilities to `marketplace_sources`.
- [x] Remove overlapping price triggers from the full `marketplace` DSH skill.
- [x] Mark full-only DSH skills as requiring `RU_MARKETPLACE_MCP_FULL=1` and a restart.
- [x] Add regression tests and update the public contract snapshot.
- [ ] Add a middle DSH preset between compare-only and full mount; measure its wire cost.
- [ ] Add fixture-based positive and negative routing evaluations for DSH skills.
- [ ] Add exact-product matching confidence using model identifiers where a source exposes them.
- [ ] Move long MCP workflow prose into skills where the wire probe proves it is duplicated.

## 5. Open Questions & Caveats

The configured A6 web-search connector rejected all three research queries with
`auth_missing`, so this run could not triangulate current marketplace-agent
industry sources through that route. Direct retrieval of the official MCP tools
specification and the repository’s own source-of-truth artifacts remained
available. Claims about this codebase are therefore high confidence; broader
industry trend claims are intentionally limited.

The report does not claim that a title heuristic can establish legal product
identity, delivery cost, coupon eligibility, or regional stock. Those require a
card-level follow-up and, in some cases, the operator’s logged-in browser.

## Methodology

The scope was product value at the MCP/DSH boundary: comparison correctness,
capability routing, context cost, and operational truthfulness. I inspected the
workspace architecture, comparison models and adapters, public contract tests,
DSH manifests and skills, the wire-cost probe, and the existing release/CI
gates. Two read-only DSH audit passes were attempted; one completed with runtime
wire measurements and one repository exploration pass was rate-limited. A6
web-search was attempted with three independent queries and rejected with
authentication errors; the report records that degradation rather than treating
uncorroborated web snippets as evidence.

The implementation was verified with targeted comparison and marketplace tests,
skill parity tests, contract snapshot regeneration, and static checks. The
changes preserve the raw ranking and add explicit fields rather than silently
filtering offers.

## Bibliography

[1] Model Context Protocol — Tools specification — https://modelcontextprotocol.io/specification/2025-06-18/server/tools — Accessed 2026-09-09 — Tier: 1
[2] Vladimir-Human — ru-marketplace-mcp repository — https://github.com/Vladimir-Human/ru-marketplace-mcp — Accessed 2026-09-09 — Tier: 1
[3] ru-marketplace-mcp — `docs/ARCHITECTURE.md`, `SECURITY.md`, connector tests — local checkout — Accessed 2026-09-09 — Tier: 1
[4] ru-marketplace-mcp — `scripts/mcp_wire.py` measurements and `dsh/cordis.patch.yml` — local checkout — Accessed 2026-09-09 — Tier: 1

## Source Extracts

### [1] Model Context Protocol Tools

- **Summary:** The official tools contract makes tool discovery, typed schemas,
  descriptions, and structured results part of the server/client interaction.
- **Key use:** Supports treating DSH routing and schema context cost as product
  behavior rather than incidental implementation detail.
- **Source type:** Official protocol specification.
- **Credibility tier:** 1

### [2] Repository release and runtime surface

- **Summary:** The repository provides 14 source-facing servers, a unified mount,
  comparison, doctor, fixture tests, and cross-platform CI.
- **Key use:** Defines the current implementation surface and validation gates.
- **Source type:** First-party repository.
- **Credibility tier:** 1

### [3] Architecture and tests

- **Summary:** The architecture documents partial failures, `None`-instead-of-zero
  price semantics, CDP boundaries, and source-specific tests.
- **Key use:** Anchors the trust and safety constraints for the new fields.
- **Source type:** First-party documentation and tests.
- **Credibility tier:** 1

### [4] DSH wire measurements

- **Summary:** The local wire probe measured the cheap compare mount at roughly
  0.9k tokens/request and the full mount at roughly 13.8k, with descriptions as a
  large share of the full cost.
- **Key use:** Justifies routing price questions to compare-only and planning a
  middle preset.
- **Source type:** First-party measurement script and DSH patch.
- **Credibility tier:** 1
