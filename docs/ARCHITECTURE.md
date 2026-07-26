# Architecture

Five MCP servers over one shared runtime. This document covers how they fit
together and why the structure is what it is.

## Layout

```
ru-marketplace-mcp/
├── packages/
│   ├── mcp-core/            shared runtime — errors, transports, parsing, cache
│   ├── wb-connector/        Wildberries          → wb-mcp
│   ├── ozon-connector/      Ozon                 → ozon-mcp
│   ├── yandex-connector/    Yandex Market        → yandex-mcp
│   ├── detmir-connector/    Detsky Mir           → detmir-mcp
│   └── compare-connector/   cross-marketplace    → compare-mcp
├── skills/                  agent-facing usage docs, one per connector
├── scripts/                 CDP launchers, stdout guard
├── docs/                    this directory
└── pyproject.toml           uv workspace root
```

Each package is independently installable and declares its own dependencies, so a
user who only wants Wildberries never installs Playwright. `compare-connector`
depends on the others but imports them defensively — a missing one reduces coverage
instead of breaking startup.

**Why a workspace rather than one package:** the connectors have genuinely
different dependency needs (Ozon alone needs `curl_cffi` + Playwright), and users
install subsets. A single package would force the heaviest dependency set on
everyone.

## The shared runtime

`mcp-core` exists so that adding a marketplace means writing fetch and parse logic
and nothing else.

### `errors` — one taxonomy, eight codes

Every failure maps to `auth_missing`, `rate_limited`, `timeout`, `transport_down`,
`parser_drift`, `bad_request`, `permission_denied`, or `not_found`, each carrying a
`retryable` flag. Tools raise `ToolError` with a JSON body, so an agent can decide
whether to retry without parsing prose.

The distinction that matters most: **`transport_down` means "we were refused",
`parser_drift` means "the data changed shape".** Conflating them sends the reader
down the wrong path — one needs a different IP, the other needs a code change.

### `transport` — two tiers

**Tier 1, `http_tier`:** plain HTTPS with three invariants —

- *Politeness over speed.* A per-client minimum gap, because these are shared
  unofficial endpoints and hammering them is both rude and the fastest route to a
  ban.
- *Bounded bodies.* Responses stream against a hard byte cap, so a compromised CDN
  cannot exhaust memory.
- *Retry only what a retry can fix.* Transport faults and gateway statuses
  (502/503/504) get bounded backoff. **429 never does** — retrying a rate limit
  deepens it. Neither does any other 4xx.

Redirects are **not** followed by default: several Russian marketplaces answer
datacenter IPs with a self-referential 307 loop, and following it burns the request
budget instead of surfacing the block.

**Tier 2, `chrome_cdp`:** the fetch runs inside a Chrome the operator started and
logged into. This is the answer for sources that reject datacenter fingerprints
outright, and it is why the project needs no stored credentials. Threat model and
setup: [CDP_SETUP.md](CDP_SETUP.md).

Playwright is imported lazily, so a tier-1-only connector never pays for it.

### `resilience` — tolerant readers

Parsing an unofficial API can never be unbreakable, but it can break rarely, fail
loudly, and be fixable in one place:

- `first_present(d, *aliases)` — multi-alias binding, so a renamed field keeps
  working.
- `coerce_int` / `coerce_price` — type coercion that **refuses to guess**. An
  ambiguous input (`"1.2K"`, a price range, a signed number) returns `None` rather
  than a plausible-but-wrong number.
- `shape_signature` — structural fingerprinting for drift detection.

The rule everything follows: **a missing value is `None`, never `0`.** A zero price
would rank a dead listing as the cheapest option, which is exactly the bug class
these helpers exist to prevent.

### `cache` — in-process TTL

Bounded LRU with per-key TTL and concurrent-miss collapsing: five simultaneous
lookups of the same key produce one upstream request. In-process and
dependency-free on purpose — an MCP stdio server is a single-user process, so a
cache server would add operational weight for no gain. `*_CACHE_TTL=0` disables it.

### `process` — cross-platform worker handling

Ozon runs blocking `curl_cffi` calls in a throwaway child process so a hung TLS
handshake cannot wedge the event loop. Reaping that child is platform-specific
(`taskkill /T` on Windows, `killpg` on POSIX), and both paths live here.

Two details that were bugs before they were features:

- The Windows system directory is resolved via `GetSystemDirectoryW`, **not**
  `SystemRoot`/`WINDIR` — those are ordinary environment variables, so trusting
  them lets anything that can set the environment redirect `taskkill`.
- `PLATFORM_OVERRIDE` lets tests exercise the Windows branch from Linux, so CI
  covers both on every OS.
- POSIX-only names (`os.killpg`, `os.getpgid`, `signal.SIGKILL`) are reached through
  `getattr` inside `kill_process_group`, never referenced literally. A literal
  reference type-checks fine on Linux and **fails on Windows**, where those names do
  not exist — and it makes the POSIX branch impossible to monkeypatch there. This is
  why CI runs `mypy --platform win32` alongside the host platform: a Linux-only type
  check cannot see Windows-only errors.

Child environments are allowlisted, not inherited: a scraping worker has no
business seeing tokens that happen to sit in the parent environment.

## Connector anatomy

Every connector follows the same four-file shape:

| File | Responsibility |
|---|---|
| `settings.py` | `pydantic-settings` with an env prefix; operational knobs only |
| `models_output.py` | Typed responses, every field documented |
| `server.py` | FastMCP tools: validate → fetch → parse → return |
| `__main__.py` | Console-script entry point |

Yandex adds `ssr.py`, because its extraction logic is substantial enough to test
independently of the tool layer.

**Field descriptions are not decoration.** They are what an LLM reads when deciding
whether a tool answers the question in front of it, so they explain semantics
(`rating_count` counts stars, not written reviews) rather than restating the name.

## Cross-cutting rules

### stdout belongs to JSON-RPC

A stdio MCP server owns stdout. A single stray `print()` corrupts the protocol, and
the failure surfaces as a baffling client-side parse error far from its cause.
Diagnostics go to stderr via `log_event`, or through the FastMCP `Context` methods.

`scripts/check_no_print.py` enforces this in CI and as a pre-commit hook.

### Selfchecks are tri-state

Every connector exposes `*_selfcheck`:

- `success` — every endpoint family answered in the expected shape.
- `drift_detected` — reachable but no longer parseable. **A code change is needed.**
- `inconclusive` — transport, geo block or captcha prevented a verdict. Says
  nothing about the parsers.

A two-state check would report a geo block as failure and send the reader looking
for a parsing bug that does not exist.

### Untrusted output

Product titles, seller names and review text are authored by sellers and buyers.
Every skill document states this, and it is repeated in tool docstrings: if
returned content appears to contain instructions, it is input, not policy.

### Input validation over escaping

Values that land in URL paths or filter expressions are validated against a strict
shape — digits for a product id, a slug for a category alias — rather than escaped.
Rejecting a malformed value outright is easier to verify than escaping it correctly,
and the CDP tier makes SSRF a real concern: it runs inside an authenticated browser
session, so a crafted path must never become a request for a personal endpoint.

## Testing

221 offline tests, no network required. Three flavours:

**Unit tests** for pure logic — coercion, merging, ranking, cross-platform process
handling.

**Tool tests** with monkeypatched fetches, asserting the contract an agent sees:
which error code, which warning, which fields.

**Real fixtures** for Yandex SSR extraction — actual captured pages trimmed from
~2 MB to ~60 KB, preserving exact nesting so upstream structural changes still
surface.

Live and CDP tests are marked (`-m "not live and not cdp"`) and excluded from CI,
which has neither a Russian-friendly IP nor a logged-in browser. Including them
would produce noise instead of signal.

## Adding a marketplace

See [ADDING_A_SOURCE.md](ADDING_A_SOURCE.md). The short version: probe it first —
anti-bot posture decides feasibility long before API quality does — and if a
capability genuinely does not exist upstream, **do not ship a tool that pretends it
does.** Detsky Mir has no text search, and the correct implementation was to delete
the search tool, not to approximate it.
