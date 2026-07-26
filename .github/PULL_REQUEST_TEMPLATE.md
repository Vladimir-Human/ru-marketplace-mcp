<!--
Thanks for the PR. Keep the summary concrete: what changed and why.
CI runs these same checks on Ubuntu, Windows and macOS against Python 3.12 and 3.13.
-->

## What and why

<!-- One or two sentences. If this fixes an endpoint, link the issue and say whether it was drift or a block. -->

## Checks

Run locally before pushing (this is what CI enforces):

- [ ] `uv run pytest -q -m "not live"` — full offline suite passes
- [ ] `uv run ruff check .` — lint clean
- [ ] `uv run ruff format --check .` — formatting clean
- [ ] `uv run mypy packages/*/src` — types clean
- [ ] `uv run python scripts/check_no_print.py` — no stdout writes

If you touched platform-specific code (process handling, signals):

- [ ] `uv run mypy --platform win32 packages/*/src` — passes; on Linux, mypy resolves `os.killpg`/`signal.SIGKILL` as present, so a Windows-breaking reference slips through the default pass

## Project gates

- [ ] **No `print()` anywhere in server code.** A stdio MCP server owns stdout — the JSON-RPC stream lives there, and a stray print corrupts it into a baffling client-side parse error. Diagnostics go through `log_event` (stderr) or the FastMCP `Context`.
- [ ] **Tool name and signature changes are additive.** Existing tool names and signatures are a public contract wired into users' MCP client configs; renaming or reordering breaks live installs, so add rather than change.

## If this reads a new field or endpoint

- [ ] Missing values are `None`, never `0` (a fabricated price ranks a dead listing as cheapest)
- [ ] Format drift raises `parser_drift`; a block raises `transport_down` / `rate_limited` — the two are not conflated
- [ ] Tests monkeypatch the fetch layer and assert the contract an agent sees (error code, warnings, field values); network tests are marked `@pytest.mark.live`
