# Adding a marketplace

The shared runtime handles transport, errors, caching and parsing helpers, so a new
connector is mostly fetch-and-parse logic. Budget most of your time for step 1
anyway — it decides whether the rest is worth doing.

## 1. Probe before you build

Anti-bot posture, not API quality, determines feasibility. Megamarket has the
cleanest API of any source evaluated for this project and is unusable; Wildberries
has a messy one and works perfectly.

Answer these five questions with real HTTP responses, not assumptions:

1. **Does anything answer anonymously?** Try the internal endpoints the web client
   uses, with a realistic Chrome `User-Agent` and `Accept-Language: ru-RU`.
2. **What does the block look like?** A 403 is different from a redirect loop is
   different from a JS proof-of-work. Loops (`?...&__rr=1`) mean IP reputation;
   proof-of-work means you need a real browser.
3. **Are all three data families reachable?** Search, product detail, reviews. A
   source with detail but no search cannot be discovered through. Lamoda is the
   case to study: its GraphQL endpoint answers card queries over plain HTTPS but
   offers no text search, so search had to go through the CDP tier. That split is
   why the connector carries two transports instead of one.
4. **Does the "search" actually search?** Send a distinctive query and read the
   results. Detsky Mir's API accepts text filters and ignores them, returning its
   entire 300k catalog. See [ANTI_BOT.md](ANTI_BOT.md).
5. **What happens under load?** Five to ten rapid requests. Note the rate limit and
   whether a captcha appears.

Record the answers, including the negatives. A precise "this returns 403 even with
full browser headers" is worth more than an optimistic guess.

## 2. Decide the transport tier

**Tier 1 (anonymous HTTP)** if plain HTTPS works. Use
`mcp_core.transport.http_tier`.

**Tier 2 (authenticated Chrome)** if the source rejects datacenter fingerprints.
Use `mcp_core.transport.chrome_cdp` and add a host allowlist — it runs inside a real
session, so a crafted input must never reach a personal endpoint. See
[CDP_SETUP.md](CDP_SETUP.md).

**Neither** if it needs a reversed binary protocol or per-request proof-of-work.
Document the refusal in `ANTI_BOT.md` and stop. Two of six candidates ended here.

## 3. Scaffold the package

```
packages/<name>-connector/
├── pyproject.toml
├── src/<name>_connector/
│   ├── __init__.py
│   ├── __main__.py          console-script entry point
│   ├── models_output.py     typed responses
│   ├── settings.py          env-prefixed config
│   ├── server.py            FastMCP tools
│   └── py.typed             PEP 561 marker — do not omit
└── tests/
    ├── conftest.py          needed: test basenames collide across packages
    └── test_server.py
```

`py.typed` matters more than it looks: without it, mypy treats every cross-package
import as `Any` and reports phantom errors elsewhere in the tree.

`pyproject.toml`:

```toml
[project]
name = "<name>-connector"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["mcp-core", "fastmcp>=3.4.0", "httpx>=0.27", "pydantic>=2.6", "pydantic-settings>=2.2"]

[project.scripts]
<name>-mcp = "<name>_connector.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/<name>_connector"]

[tool.uv.sources]
mcp-core = { workspace = true }
```

Then register it in the workspace root `pyproject.toml`: add to `dependencies`,
`[tool.uv.sources]`, and `[tool.pytest.ini_options] testpaths`.

## 4. Write the tools

Four rules carry most of the quality:

**Validate inputs by shape, don't escape them.** A product id that lands in a URL
path is checked with `.isdigit()`; a category alias is matched against a slug
pattern. Rejecting a malformed value is easier to verify than escaping it correctly.

**A missing value is `None`, never `0`.** Use `coerce_price`/`coerce_int` from
`mcp_core.resilience`. A zero price ranks a dead listing as the cheapest option —
the exact bug these helpers exist to prevent.

**Write field descriptions for a reader who cannot see the API.** They are what an
LLM uses to decide whether your tool answers the question. Explain semantics, not
names: "counts star ratings, not written reviews, which is usually far smaller".

**Warn instead of hiding.** Partial data, a fallback path, an empty result — all
belong in `meta.warnings` with `healthy: false`. Silence reads as success.

Every tool needs `ToolAnnotations(readOnlyHint=True, destructiveHint=False,
idempotentHint=True, openWorldHint=True)` and an `## Error Format` note in its
docstring.

## 5. Add a tri-state selfcheck

```python
async def <name>_selfcheck(ctx=None) -> SelfcheckResponse:
    # success        — every family answered in the expected shape
    # drift_detected — reachable but unparseable → code change needed
    # inconclusive   — transport/geo/captcha → says nothing about parsers
```

Chain the probes where you can: have the search probe supply a live id for the card
probe, so the canary never depends on a hardcoded SKU that may be delisted.

## 5a. If you read a rendered page, reuse the shared extractor

Do not write your own DOM helpers. `mcp_core.dom` exports `JS_HELPERS`; splice it
into your extractor and you inherit four things that were each learned the hard
way on a live site:

```python
from mcp_core.dom import JS_HELPERS, prices_from_tile, title_from_tile

_SEARCH_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    ...
}
"""
_SEARCH_EXTRACT_JS = _SEARCH_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)
```

| helper | what it saves you from |
|---|---|
| `tileRootFor(anchor, idRe)` | `closest()` tests the element *itself* first, so an image link whose class contains "product" becomes the tile — and the tile reads as empty |
| `priceTextsIn(root)` | splits numbers by whether a currency glyph is attached; a bare number never becomes the price |
| `cleanText` / `cleanTextWithout` | `textContent`, not `innerText` — the latter depends on layout and is unavailable in the test harness |
| `DECOY_RE` | instalments, bonuses, discount badges, delivery counts and ratings, generated from one Python list |

**The extractor must not do arithmetic.** Return the display strings and let
`prices_from_tile` decide: `coerce_price` already understands non-breaking
spaces, a missing glyph and comma decimals, and refuses an ambiguous
multi-number blob instead of inventing a value.

The one rule to carry: **the price is the number attached to a currency glyph.**
A tile also shows "от 5 751 ₽/ мес.", "- 10%", "в 1356 пунктов" and a rating. A
parser that takes the smallest number reports the monthly instalment, and that
answer validates, looks right, and is wrong.

## 6. Test offline

Never let a test touch the network. Monkeypatch the fetch layer and assert the
contract an agent sees — which error code, which warning, which fields.

For HTML/SSR sources, capture a real page and **trim it** rather than inventing
markup: preserve the exact nesting so upstream structural changes still surface.
The Yandex fixtures went from ~2 MB to ~60 KB this way.

**Test the extractor against that markup, not around it.** Mocking the render
call and feeding a pre-parsed dict leaves the parser itself uncovered — that is
exactly where the DNS and Citilink bugs lived while 707 tests stayed green.
`mcp_core.domtest` runs your real extractor over a fixture in jsdom:

```python
from mcp_core.domtest import JsdomUnavailable, run_extractor


def _extract(js_source):
    try:
        return run_extractor(js_source, FIXTURE, page_url="https://example.ru/search/")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
```

jsdom is a developer tool, not a dependency: without it the DOM half skips and
the pure-Python price-selection assertions still run. Write both halves.

Record in the fixture header what the page *displayed* when you captured it —
price, old price, availability. That turns the fixture from a blob into evidence
someone can re-check.

Mark anything that needs the network `@pytest.mark.live` and anything needing a
browser `@pytest.mark.cdp`; CI excludes both.

## 7. Wire it up

- **`skills/<name>-connector/SKILL.md`** — required, and enforced:
  `packages/marketplace-connector/tests/test_skills_parity.py` fails the build if
  a connector has no skill, if the skill omits a tool the server exposes, or if it
  names a tool that does not exist. Cover when to use it, the workflow patterns,
  and the gotchas from step 1 — and be explicit about what the source *cannot* do
  and which of its answers need a second look. "Run the selfcheck" is not a
  verification story: a green selfcheck proves the transport answered, not that
  the parser understood it.
- **`compare-connector`** — add a `_search_<name>` adapter and an entry in
  `_SEARCH_IMPLS`, but only if the source has a working text search. If it does not,
  leave it out of `SEARCHABLE` and say why in a comment.
- **CI** — bump the expected tool count in `.github/workflows/ci.yml` and add the
  console script to the smoke step.
- **README and CHANGELOG** — the tool table and the release notes.

## 8. Verify

```bash
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -q -m "not live and not cdp"
uv run python scripts/check_no_print.py
```

Then run your selfcheck live and confirm the tool inventory:

```bash
uv run python -c "
import asyncio
from <name>_connector.server import mcp
print(asyncio.run(mcp.list_tools()))
"
```

## The rule that matters most

**If a capability does not exist upstream, do not ship a tool that pretends it
does.**

Detsky Mir has no text search. A `detmir_search` tool was written, tested against
live data, and deleted — for the query "лего" it returned nappies, dishwashing
liquid and a collagen supplement, each with a correct price and rating. That
plausibility is what made it dangerous: an error is diagnosable, a confident wrong
answer is not.

Document the absence in the tool descriptions so an agent stops looking for a tool
that should not exist.
