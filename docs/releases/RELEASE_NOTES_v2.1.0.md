# v2.1.0

Minor release: evidence-aware comparison layer and a middle DSH profile.

## Added

- Middle DSH mount `ru-marketplace-decision` via the new `decision-mcp` console
  script: the cheap comparison trio plus one generic card inspector
  `decision_inspect(source, product_id_or_url)`. A DSH agent can verify the
  comparison winner, seller and card details without paying for the full
  37-tool marketplace mount. The row ships disabled by default and is enabled
  with `RU_MARKETPLACE_MCP_DECISION=1` (mutually exclusive with the full mount).
- `compare_prices` offers now carry `identity` (brand, model, MPN, GTIN,
  variant attributes) and optional `evidence` provenance, so exact-product
  matching is checkable instead of assumed; the raw cheapest offer is kept
  separate from the comparable candidate as before.
- Reliability scripts: deterministic DSH routing contract eval
  (`scripts/routing_eval.py`), operational gates (`scripts/test_ops_gates.py`),
  and `scripts/mcp_wire.py` baseline/snapshot modes that fail CI on wire-token
  or latency regressions.
- CDP hardening: bounded websocket frames and a final-host allowlist
  (`_check_final_host`) so post-navigation redirects cannot drift off the
  connector's configured marketplace hosts.

## Changed

- `e2e_stdio_check.py` now covers 15 stdio servers including `decision-mcp`
  (4 tools); the DSH bundle CI guard asserts all 3 MCP rows are disabled by
  default.
- Documented offline test count is 1243 across the repository.

## Verification

- Full offline suite passed on Linux, macOS and Windows (Python 3.12/3.13).
- `e2e_stdio_check.py`: 15/15 real MCP sessions at this version.
- ruff, mypy (host/win32/darwin), coverage >= 70% gate, lockfile, version
  consistency (79 declarations) and test-count gates all passed.

Live marketplace data was not freshly re-verified by this release; anti-bot
challenges remain honestly typed `blocked/inconclusive`.