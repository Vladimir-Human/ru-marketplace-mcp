# Authenticated transport: driving your own Chrome

Some marketplaces refuse datacenter traffic outright. Ozon answers
`composer-api.bx` with an endless 307 redirect loop; no amount of TLS impersonation
clears it. The reliable answer is not a better fingerprint — it is to run the fetch
**inside a browser you already trust**, over the Chrome DevTools Protocol.

Only Ozon needs this, and only when Cloudflare challenges. Wildberries, Yandex
Market and Detsky Mir never do.

## Read this before you enable it

CDP hands **any local process full control of the Chrome profile it is attached
to**, including every session logged into that profile. If you attach it to your
daily browser, a bug or a hostile local process inherits your banking, email and
work sessions.

So the setup below uses a **dedicated scraping profile**. That is not a nicety; it
is the entire mitigation. The other three layers:

| Mitigation | What it prevents |
|---|---|
| Separate `--user-data-dir` | Blast radius beyond the marketplaces you log into |
| `--remote-debugging-address=127.0.0.1` | Any LAN access to the debugging port |
| Scheme guard in `open_page` | An authenticated browser being aimed at `file:///` |
| Per-connector host allowlists | A crafted SKU becoming a request for `/api/personal/orders` |

**No credentials are ever stored, read or transmitted by this project.** You log in
by hand, in a browser you control. There is no credential store to leak.

## Setup

### 1. Start Chrome with remote debugging

**Windows (PowerShell):**

```powershell
.\scripts\start_chrome_cdp.ps1
```

**Linux / macOS:**

```bash
./scripts/start_chrome_cdp.sh
```

Both create a scraping-only profile and bind the debugging port to localhost:

| Platform | Default profile location |
|---|---|
| Windows | `%LOCALAPPDATA%\Chrome-Scraping` |
| macOS | `~/Library/Application Support/Chrome-Scraping` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/chrome-scraping` |

Useful flags: `--port 9333`, `--profile /path/to/dir`, `--headless`.

Headless is off by default and should stay off: anti-bot systems detect headless
Chrome readily, which defeats the purpose of using a real browser.

### 2. Log into Ozon — and nothing else

In the window that opens, sign into `ozon.ru`. **Do not** sign into banking, email,
or work accounts in this profile. Keeping it single-purpose is what bounds the risk.

### 3. Verify

```bash
curl -s http://127.0.0.1:9222/json/version
```

You should see Chrome's version JSON. On Windows you can also use
`Test-NetConnection 127.0.0.1 -Port 9222`.

### 4. Confirm the connector sees it

```bash
uv run python -c "
import asyncio
from ozon_connector.server import ozon_selfcheck
print(asyncio.run(ozon_selfcheck()).status)
"
```

`success` means both tiers work. `inconclusive` with a transport message means
Chrome is not reachable — recheck the port. `drift_detected` means Ozon changed its
payload shape and the connector needs updating.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CHROME_CDP_PORT` | `9222` | Debugging port |
| `CHROME_SCRAPING_PROFILE` | platform default above | Profile directory |
| `CHROME_BINARY` | auto-detected | Explicit path to Chrome/Chromium/Edge |
| `CHROME_STEALTH` | `1` | Windows: park the window off-screen so it never steals focus |
| `CHROME_HEADLESS` | `0` | Headless mode — detectable, use only on a display-less host |

Auto-detection covers Chrome, Chromium and Edge in the standard locations for all
three platforms. On Linux it also consults `PATH`.

## How it behaves at runtime

The connector starts Chrome automatically on first use if the port is not already
listening, so the launcher scripts are optional convenience. It waits up to 12
seconds for the port to bind.

Chrome is **detached, never closed** by the connector — it is your browser. Each
fetch opens a tab, navigates, reads, and closes that tab.

Every operation is individually bounded (tab creation, navigation, teardown), so a
wedged CDP session cannot hang a tool call indefinitely. A block, auth wall or 5xx
main document raises `NavBlocked` rather than handing a login page to a parser as
though it were data — Playwright resolves `goto()` for those statuses, so this check
is what stands between you and silently parsing a captcha page.

## Troubleshooting

**"Chrome/Chromium not found"** — set `CHROME_BINARY` to the full executable path.

**"CDP did not bind within 12s"** — another Chrome instance is likely holding the
same profile directory. Close it, or use a different `--profile`.

**Port already in use** — something else has 9222. Use `--port 9333` and set
`CHROME_CDP_PORT` to match.

**Ozon still blocked with CDP running** — confirm you are logged into ozon.ru *in
the scraping profile* (not your main browser), and that the session has not
expired. Open the page manually in that window to check.

**Running in a container as root** — Chrome's sandbox refuses to run as root; the
launcher adds `--no-sandbox` automatically when it detects that case.

## Should you use this at all?

If you only need Wildberries, Yandex Market and Detsky Mir: **no**. All three work
over plain anonymous HTTP, and `compare_prices` will simply report Ozon as blocked
and rank the rest.

Enable it when Ozon data specifically matters to you, and when you are comfortable
with the trade-off above. A residential IP is the alternative — from a Russian
residential address, Ozon's tier 1 often works without any browser at all. Set
`OZON_PROXY` or the standard `HTTPS_PROXY` to route through one.
