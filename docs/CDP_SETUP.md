# Authenticated transport: driving your own Chrome

Some marketplaces refuse datacenter traffic outright. Ozon answers
`composer-api.bx` with an endless 307 redirect loop; no amount of TLS impersonation
clears it. The reliable answer is not a better fingerprint — it is to run the fetch
**inside a browser you already trust**, over the Chrome DevTools Protocol.

Nine sources use this: Ozon, Avito, Taobao, Megamarket, Lamoda, DNS, Citilink,
AliExpress and Cian.
The split matters. Taobao, Megamarket, DNS, Citilink, AliExpress and Cian are
**CDP-only** — no anonymous tier exists, so nothing reads from them without a logged-in Chrome. Ozon
and Avito search use CDP as a **tier-2 fallback**: tier 1 (TLS impersonation) usually works
from a Russian residential IP, and CDP only kicks in when the anonymous tier is
challenged; Avito cards and seller profiles always read the rendered page in Chrome
(since 2026-10-12). Lamoda is split — cards try anonymous GraphQL first and fall back
to Chrome when it is refused (the 307 loop, 2026-10-12), search needs CDP.
Wildberries search and cards also use CDP in `WB_TRANSPORT=dom`, or when their
configured API-to-DOM fallback runs. Yandex Market and Detsky Mir do not use CDP.

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
by hand, in a browser you control. There is no credential store to leak. (The
optional MPStats connector is the one exception — you supply `MPSTATS_MP_AUTH`
yourself via env; it is never stored by the project either.)

## Setup

### Optional challenge handoff

Set `CHROME_CHALLENGE_HANDOFF_S=120` in the MCP server's environment to retain
DOM-detected Lamoda search and Taobao search/card challenge tabs for up to two
minutes. Default `0` disables retention; the hard maximum is 300 seconds, including
initial browser attachment/navigation. A headed Chrome and a client the server can
recognise between calls are required: an MCP session on handshake-era connections
(protocol 2025-11-25 and older), or, on sessionless 2026-07-28 connections, the
stdio server process itself, since one stdio process serves one client. A
sessionless HTTP connection names no client between requests, so there the
challenge is reported without retaining the tab. This mode does not solve CAPTCHA
or log into accounts.

An error with `handoff_expires_at` confirms a retained tab. Complete the interaction
in that tab, then repeat the same operation and arguments from the same client.
The connector reads that exact page without navigating again; a successful read
closes it. For comparison, repeat the original query with only the affected source.
Pending handoffs take precedence over successful-result caches, so another
session's cached response cannot bypass the retained page.
The original expiry never extends on retries. Different sessions and operations
receive independent tabs; at most four handoffs are active per process. Concurrent
reads of one lease or a full registry report a busy transport error.

The runtime attempts to reveal the owned browser window and prevents unrelated
connector calls from hiding it while the handoff is active. Foreground activation
is best-effort, especially for remote browsers. Expiry, errors, cancellation and
graceful shutdown close owned tabs; a hard process kill cannot guarantee expiry
in an external Chrome. Without `handoff_expires_at`, use the ordinary retry flow:
HTTP errors rejected before DOM extraction, headless runs, CLI calls and
sessionless HTTP calls do not retain a tab. No cookies, screenshots or browser profile copies
are written by the handoff runtime. A retained error also carries an opaque
`handoff_id`; a vision-capable MCP client may pass it to
`compare_browser_snapshot(handoff_id)` to receive the current viewport as an
image plus metadata. The snapshot does not solve or interpret the challenge.

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

**You do not need to close your everyday Chrome.** The launcher starts a second
Chrome with its own `--user-data-dir`, which Chrome allows next to another
profile. It refuses only the one case that cannot work: another Chrome already
holding *this* profile, which would refuse to start a second time. If a CDP
endpoint already answers on the port, the launcher says so and exits without
starting anything.

### Linux server without a physical display

A server can run normal, headed Chrome on a virtual X display; a monitor and a
browser on the MCP client's computer are not required. For example, on Debian or
Ubuntu with Chrome already installed, choose an unused display and run as your
ordinary user:

```bash
sudo apt-get install xvfb xauth
umask 077
export DISPLAY=:99
export XAUTHORITY="${XDG_DATA_HOME:-$HOME/.local/share}/chrome-scraping.Xauthority"
mkdir -p "$(dirname "$XAUTHORITY")"
touch "$XAUTHORITY"
xauth -f "$XAUTHORITY" add "$DISPLAY" . "$(mcookie)"
Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp -noreset -auth "$XAUTHORITY" &
export CHROME_HEADLESS=0
```

When Xvfb is ready, run `./scripts/start_chrome_cdp.sh` in that shell.
Keep `DISPLAY`, `XAUTHORITY`
and the same `CHROME_SCRAPING_PROFILE` in the environment of the MCP server too.
For unattended operation, supervise Xvfb and Chrome with your service manager,
starting Chrome after Xvfb. Run Chrome as an ordinary user with its sandbox.

If a site needs human interaction, view this server display through a VNC/noVNC
service bound to loopback and an authenticated SSH tunnel. Keep CDP and the viewer
off the public network. The browser and its persistent profile stay on the Linux
server. A virtual display does not guarantee that a site's browser check will pass.

### 2. The window and the two ways to reach it

The launcher scripts start the manual Chrome **on-screen** so you can log in.
`-Hidden` (PowerShell) parks it off-screen instead, `--hidden` (shell) hides the
app with ⌘H on macOS and changes nothing on Linux, and
`-Visible` is still accepted as a no-op. The connector's own autostart is
different: on Windows it deliberately parks the window off-screen so it never
takes focus. That is the window you cannot see, and logging in or passing a
site's check needs a human. These verbs answer that:

```console
marketplace-mcp browser start    # launch the scraping Chrome on-screen (--hidden: off-screen)
marketplace-mcp browser status   # is the endpoint reachable, which Chrome, how many tabs, where is the window
marketplace-mcp browser show     # un-minimize the window, move it on-screen, activate its first tab
```

`browser start` exits 0 when CDP answers at the end (also when it was already
running; it then points at `browser show`), and 1 with the reason otherwise.
`browser status` exits 0 when the endpoint answers and 1 when it does not, and
then prints how to start the scraping Chrome. `browser show` sends browser-level
CDP commands only: it opens no page and requests nothing from any website.

### 3. Log into the marketplaces you need — and nothing else

In the window that opens, sign into whichever CDP sources you use — `ozon.ru`,
`avito.ru`, `taobao.com`, `megamarket.ru`, `lamoda.ru`, `dns-shop.ru`,
`citilink.ru`, `aliexpress.ru`, `cian.ru`. One profile serves them all. **Do not** sign into banking, email, or
work accounts here. Keeping it single-purpose is what bounds the risk.

When `doctor` reports a wall, `marketplace-mcp unblock <source>` opens that
source's page in a new tab and waits while you pass the check or log in; it then
re-runs only that source's selfcheck. It opens pages and re-checks — it never
solves or automates a site's check.

### 4. Verify

```bash
curl -s http://127.0.0.1:9222/json/version
```

You should see Chrome's version JSON. On Windows you can also use
`Test-NetConnection 127.0.0.1 -Port 9222`.

### 5. Confirm the connector sees it

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
| `CHROME_NAV_TIMEOUT_MS` | `20000` | Initial page-navigation timeout in milliseconds, shared by Playwright and raw CDP unless a connector supplies its own value; finite and greater than zero |
| `ALI_TIMEOUT` | `45` | AliExpress search and card-landing navigation timeout in seconds; overrides the shared navigation setting; finite and greater than zero |
| `CHROME_SCRAPING_PROFILE` | platform default above | Profile directory |
| `CHROME_BINARY` | auto-detected | Explicit path to Chrome/Chromium/Edge |
| `CHROME_STEALTH` | `1` | Windows: park the window off-screen; macOS: open tabs in the background and keep the app hidden (⌘H) so it never steals focus or switches your Space |
| `CHROME_HEADLESS` | `0` | Headless mode — detectable, use only on a display-less host |
| `OZON_CHROME_COMPAT` | `native` | `windows` opts owned Ozon CDP tabs into Windows OS identity; no change to other sources or manually opened tabs |

Auto-detection covers Chrome, Chromium and Edge in the standard locations for all
three platforms. On Linux it also consults `PATH`.

Timeouts must remain finite and positive after unit conversion; a value too small
to represent as raw-CDP seconds is rejected, not rounded to a disabled timeout.
Set timeout variables in the MCP server's environment and restart the server to
reload AliExpress settings. For example, `ALI_TIMEOUT=90` allows up to 90 seconds
for its search or card landing to navigate; it does not remove any page checks.
Playwright still waits for `DOMContentLoaded`. Raw CDP keeps its existing
load-event/main-document-status behavior; changing the budget does not make a
200 response proof that product data is ready. Other connectors keep the shared
20-second default unless `CHROME_NAV_TIMEOUT_MS` is set.

These are navigation limits, not end-to-end tool limits. CDP attachment, settling,
extraction and teardown have separate bounds. `compare_prices` still applies
`COMPARE_SOURCE_TIMEOUT` (45 seconds by default) to the whole source call, and
AliExpress selfchecks keep their 90-second outer limit. A larger navigation setting
does not override those ceilings. If a normal page needs more time, increase the
appropriate budgets rather than blocking the site's anti-bot resources.

### Optional Ozon compatibility on Linux

In a November 2026 Linux-server check, Ozon returned HTTP 403 with a suggestion to
disable VPN even on a direct Russian residential connection. On the same server
and public IP, a fresh dedicated Chrome profile with Windows OS identity could
read the catalog, search and a current product card. This is an observation from
one environment, not proof that every such 403 is caused by the OS identity.

To try that mode, set this in the **MCP server's** environment and restart its
process. Reconnecting a stdio client is sufficient only if it starts a new server
process; reconnecting to an already running HTTP server does not reload settings:

```bash
export OZON_CHROME_COMPAT=windows
```

Only the Ozon connector's owned CDP tabs opt in, before their first navigation.
The override presents Windows in the user agent, `navigator.platform` and UA client
hints while retaining the actual browser's version and brands and leaving language
unchanged. It applies in both Playwright and raw-CDP fallback. Initial and final
origins must be exactly HTTPS `ozon.ru` or `www.ozon.ru` on the default HTTPS port.
Other sources, existing tabs and manually opened `unblock` pages keep their native
identity. The anonymous HTTP tier is unchanged.

An already rejected scraping profile may still fail. To compare, stop only that
dedicated Chrome and start it with a **new, separate** `CHROME_SCRAPING_PROFILE`;
keep the original profile intact, and use the same new path for the launcher and
MCP process. This is a diagnostic option, not a promise of access. Real 403s,
CAPTCHAs, host-policy checks, pacing and cooldowns still apply; this mode neither
changes your IP nor solves a challenge. Set `OZON_CHROME_COMPAT=native` and restart
the MCP process to restore the default behavior.

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
expired. Open the page manually in that window to check. A generic "disable VPN"
message is not a routing diagnosis: check the actual egress/proxy settings first.
On Linux, see the optional Ozon compatibility mode above.

**Running in a container as root** — Chrome's sandbox refuses to run as root; the
launcher adds `--no-sandbox` automatically when it detects that case.

## Should you use this at all?

If your Wildberries API requests work and you only need Wildberries, Yandex Market
and Detsky Mir, plain anonymous HTTP may be enough. If WB's API is refused while
the rendered website works, use the DOM transport. `compare_prices` reports CDP
sources it cannot reach as blocked and ranks the sources it can read.

You need it the moment Taobao, Megamarket, DNS, Citilink, AliExpress or Cian matter —
those six have no anonymous tier, so without a logged-in Chrome they return nothing at all. For
Ozon and Avito search it is optional insurance: tier 1 usually answers, and CDP only earns
its keep when the anonymous tier is challenged; Avito cards and sellers need it. For
Lamoda, cards work without it while anonymous GraphQL answers, search never does.

A residential IP is the alternative for the fallback sources — from a Russian
residential address Ozon's and Avito's tier 1 often work without any browser. It
does nothing for the CDP-only six, whose block is at the API, not the IP. Set
`OZON_PROXY`/`AVITO_PROXY` or the standard `HTTPS_PROXY` to route through one.
