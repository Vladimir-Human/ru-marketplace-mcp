# v2.0.0 evaluation matrix

## Binary gates

| Dimension | Cases | Oracle |
|---|---|---|
| Product correctness | golden search/card/compare; null/range/negative/zero; out-of-stock; mixed malformed items | no fabricated price/currency/stock; valid records survive |
| Routing | 200, 429, 5xx, timeout, challenge, JSON block, tier-2 failure | transport vs parser drift vs inconclusive classified correctly |
| Drift | renamed field, moved list, selector move, wall/login, late render, legitimate empty | exact tri-state verdict and diagnostic reason |
| Partial failures | one source/item fails | per-source outcome, warning, valid records preserved |
| Doctor | every source state, status file, raised selfcheck | exit precedence and redaction invariants |
| Security | synthetic secrets, redirects, tenant/CDP isolation, prompt injection | no secret in logs/errors; no off-host navigation; external text remains data |

## Threshold gates

- latency: p50/p95/max for cold and warm stdio sessions; p95 ≤10s, max ≤30s;
- wire cost: versioned profile baseline; regression threshold +10%;
- schema: no single tool over 25% of profile cost without explicit exception;
- parser fixtures: 100% critical fields, ≥98% valid records, zero silent fabrication;
- trace record: commit, profile, fixture, route, timing, tokens, verdict.

Do not collapse these into one score. A good median cannot compensate for a
secret leak, false cheapest winner, or false healthy drift result.
