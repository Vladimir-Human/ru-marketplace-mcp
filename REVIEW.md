# REVIEW.md: what matters when reviewing this repository

A guide for code reviewers, human or model. Read-only MCP connectors for Russian marketplaces
(Python 3.12+, uv workspace, shared `mcp-core`). LLM agents use the answers to compare prices,
so a confident wrong answer is worse than an error. Requirements: `.grace/context/requirements.xml`;
background: `CONTRIBUTING.md` ("What this codebase cares about") and `SECURITY.md`.

## Treat as serious (P0-P1)

- **A fabricated value.** A missing or ambiguous price or count becomes `0` or a guess; an
  unavailable price becomes the cheapest offer; the price kind or a conditional price is lost
  (R-NO-FABRICATION). Missing is `None`; use `coerce_price` / `coerce_int`.
- **A write or a stored credential.** Anything that orders, reviews or changes a marketplace
  account, or a new stored marketplace credential (R-READ-ONLY, R-CREDENTIALS; the optional
  `MPSTATS_MP_AUTH` token is the one documented exception).
- **A weaker CDP tier.** The browser runs in the operator's logged-in profile: an input that
  reaches a URL path or filter without strict shape validation, a looser host allowlist, scheme
  guard or origin/redirect guard, or automatic captcha solving (R-INPUT-TRUST, SECURITY.md).
- **stdout in MCP server code.** A stray `print()` corrupts the stdio JSON-RPC stream (R-STDOUT).
- Seller or buyer text acted on as instructions instead of data.

## Also check

- Errors stay apart: `parser_drift` (data changed shape) vs `transport_down` (we were blocked)
  vs `rate_limited`, `challenge_required`, `not_found`. Only `rate_limited`, `timeout`,
  `transport_down` and `challenge_required` are retryable. A blocked selfcheck must not report
  a healthy parser (R-DRIFT, R-ERRORS).
- Politeness: pacing gaps, bounded bodies and retries (the shared HTTP tier does not retry 429
  or other 4xx), browser concurrency budgets (R-TRANSPORT).
- The public contract: no tool, argument or response field renamed, removed or retyped without
  an intentional snapshot update; field descriptions explain meaning, not names (R-TOOL-SCHEMA).
- Tests run offline by default (`live` and `cdp` markers for the rest); fixtures are captured
  real pages with provenance pins, never invented markup (R-OFFLINE, R-FIXTURES).
- Windows, Linux and macOS: process termination behind seams, POSIX-only calls guarded
  (R-CROSS-PLATFORM).
- One version across every declaration (`scripts/check_versions.py`); `.grace/` updated with
  any behaviour change (R-VERSION, R-GRACE).
- A tool that pretends to offer what the upstream source cannot do.

## Not worth a finding

Formatting and lint that ruff and the CI gates already enforce; naming taste.
