# Contributing

Thanks for considering a contribution. This project reads unofficial marketplace
endpoints, which shapes what "good" looks like here — the notes below are mostly
about that.

## Setup

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q
```

Optionally install the hooks so a commit fails fast rather than in CI:

```bash
uv run pre-commit install
```

## Before you open a PR

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src    # catches Windows-only type errors
uv run pytest -q
uv run python scripts/check_no_print.py
```

CI runs the same checks on Ubuntu, Windows and macOS against Python 3.12 and 3.13.

**Run the cross-platform mypy pass if you touch anything platform-specific.** On
Linux, mypy resolves `os.killpg`, `os.getpgid` and `signal.SIGKILL` as present, so a
Windows-breaking reference passes review invisibly. Reach POSIX-only names through
`getattr`, and keep the platform branch behind a patchable seam so tests can exercise
it from any host.

## What this codebase cares about

**Never fabricate a value.** A missing price is `None`, never `0`. A zero would rank
a delisted item as the cheapest option — the single most damaging bug class in a
price tool. Use `coerce_price`/`coerce_int` from `mcp_core.resilience`; they return
`None` on ambiguous input rather than guessing.

**Fail loudly, not plausibly.** When a payload stops matching, raise `parser_drift`.
A confident wrong answer is worse than an error, because an error is diagnosable.

**Distinguish "refused" from "changed".** `transport_down` means we were blocked;
`parser_drift` means the data changed shape. They need completely different fixes,
so conflating them sends the reader down the wrong path.

**Never write to stdout.** A stdio MCP server owns stdout — a stray `print()`
corrupts the JSON-RPC stream and surfaces as a baffling client-side parse error. Use
`log_event` (stderr) or the FastMCP `Context` methods. Enforced by
`scripts/check_no_print.py`.

**Validate inputs by shape.** Values reaching URL paths or filter expressions are
checked against a strict pattern (digits, slug) rather than escaped. This matters
especially for the CDP tier, which runs inside an authenticated browser session.

**Write field descriptions for someone who cannot see the API.** They are what an
LLM reads when deciding whether a tool fits the question. Explain semantics, not
names.

**If a capability does not exist upstream, do not ship a tool that pretends it
does.** See the Detsky Mir search case in [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

## Tests

Every test must run offline. Monkeypatch the fetch layer; assert the contract an
agent sees — error codes, warnings, field values.

For HTML/SSR sources, capture a real page and trim it rather than inventing markup,
so upstream structural changes still surface. Mark network tests
`@pytest.mark.live` and browser tests `@pytest.mark.cdp`; CI excludes both.

## Reporting a broken endpoint

Upstream endpoints break — that is expected, not a defect in your report. Please
include:

1. The connector and tool.
2. Output of the relevant `*_selfcheck` (it distinguishes drift from a block).
3. Whether you are on a Russian residential IP, a datacenter IP, or a VPN. This is
   usually the deciding factor.
4. The error JSON, redacted if it contains anything personal.

`inconclusive` from a selfcheck usually means geo blocking rather than a code bug.
[docs/ANTI_BOT.md](docs/ANTI_BOT.md) covers what each source does from where.

## Adding a marketplace

See [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md). Probe the source first and
share the findings in the issue before writing code — roughly a third of candidates
turn out to be infeasible, and that is worth knowing early.

## Scope

In scope: read-only public catalog data, reliability work, new marketplaces that pass
the feasibility probe, better agent-facing documentation.

Out of scope: anything requiring stored credentials or an account; write operations
(placing orders, posting reviews); captcha-solving services; bulk scraping at a rate
these politeness limits are designed to prevent.
