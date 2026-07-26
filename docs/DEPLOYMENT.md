# Deployment

Two ways to run the connectors. Both are optional; neither changes the default.

- **stdio** (default): the MCP client spawns the server as a subprocess and
  talks over stdin/stdout. This is what every client config in the README does,
  and it is unchanged — if you do nothing here, nothing about your setup moves.
- **HTTP** (opt-in): the server listens on a port and speaks MCP over
  streamable HTTP, for running it remotely or in a container. You turn it on
  with one environment variable.

The servers are read-only catalog scrapers with **no authentication of their
own**. That fact drives every security note below; read them before you expose
anything.

## Transport selection

Selection is environment-driven and lives in `mcp_core.runtime`, shared by all
five entry points so they behave identically.

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`, `http`, `streamable-http`, or `sse`. `http` is the modern streamable-HTTP transport; the other two are FastMCP's own aliases, kept for operators who know them. |
| `MCP_HTTP_HOST` | `127.0.0.1` | Bind address. HTTP only. Loopback by default — see below. |
| `MCP_HTTP_PORT` | `8000` | Bind port. HTTP only. |
| `MCP_HTTP_PATH` | `/mcp` | Endpoint path. HTTP only. |

An unset or empty `MCP_TRANSPORT` is stdio. An unrecognised value is rejected at
startup rather than falling back, because a silent fallback would start a stdio
server for someone who asked for HTTP and the failure would look like "the port
never opened" — miles from the typo that caused it. Host and port are ignored
under stdio, so a stray `MCP_HTTP_PORT` cannot break the default launch.

The transport string is verified against the installed FastMCP
(`inspect.signature(FastMCP.run)`), which accepts `"http"`, `"stdio"`, `"sse"`,
and `"streamable-http"`. `host`/`port`/`path` flow through `FastMCP.run(...)`
into its HTTP runner.

Diagnostics go to **stderr only**. Under stdio, stdout is the JSON-RPC stream and
a single stray byte there corrupts the protocol; `scripts/check_no_print.py`
enforces this across the connector source, `mcp_core.runtime` included.

### stdio (default, unchanged)

Nothing to configure. The README's client configs already do this:

```json
{"command": "uvx", "args": ["--from", "wb-connector", "wb-mcp"]}
```

### HTTP (opt-in)

```bash
MCP_TRANSPORT=http MCP_HTTP_HOST=127.0.0.1 MCP_HTTP_PORT=8000 wb-mcp
```

The endpoint is then `http://127.0.0.1:8000/mcp`. A client initialize over that
endpoint returns the server info; a bare `GET /mcp` without a session returns a
well-formed JSON-RPC 400 (`Missing session ID`), which is the endpoint telling
you it is alive and speaking MCP.

## Security posture

**The default bind host is `127.0.0.1` on purpose.** These servers carry no auth.
On `0.0.0.0` — or any routable address — an HTTP MCP server is an
unauthenticated scraper that anyone who can reach the port may drive. The tools
only read public catalog data, so this is not a data-exfiltration hole, but it
is still an open egress endpoint running requests on your behalf and against a
marketplace's terms. Loopback keeps it on your machine until you decide
otherwise.

If you bind beyond loopback, the server starts but logs a loud `http_bind_exposed`
warning to stderr — exposure is never silent. Before you expose one for real:

- Put an **authenticating reverse proxy** (nginx, Caddy, an API gateway) in
  front and let it terminate TLS and enforce auth. The MCP server itself will
  not.
- Keep the server bound to loopback and point the proxy at it, or bind it to a
  private interface the proxy can reach — not to `0.0.0.0` on a public host.
- Rate-limit at the proxy. A polite request rate is a condition of reading these
  endpoints at all.

## Docker

A multi-stage `Dockerfile` and a `docker-compose.yml` are included. The image
installs the workspace with uv into a virtualenv in a builder stage, then copies
only that virtualenv and the source into a plain `python:3.12-slim-trixie`
runtime — uv does not ship in the runtime image. It runs as a **non-root** user,
because a network-reachable scraper has no business being root in its container.

Image tags are pinned: the builder is `ghcr.io/astral-sh/uv:0.11.32-python3.12-trixie-slim`
(the uv version this repo's `uv.lock` was produced with) and the runtime is
`python:3.12-slim-trixie`. Both were confirmed to exist in their registries.

### Build

```bash
docker build -t ru-marketplace-mcp:1.1.0 .
```

The install uses `uv sync --all-packages --frozen`: `--all-packages` installs
every workspace member, `--frozen` uses `uv.lock` exactly and never re-resolves,
so the image's dependency set matches local development bit for bit.

### Run one server

HTTP is the only transport that makes sense in a detached container: stdio needs
a client attached to the process's stdin/stdout, and nothing is attached. (If you
genuinely want stdio in a container, a client has to `docker exec -i` into it and
speak JSON-RPC over that pipe — niche, and not what these files set up.)

The image defaults to `MCP_TRANSPORT=http` and, **inside the container**,
`MCP_HTTP_HOST=0.0.0.0`. That is deliberate and is not a contradiction of the
loopback rule: inside the container `127.0.0.1` would be unreachable from the
host, so the container binds to all of its *own* interfaces and the perimeter
moves to the **published port**. Publish it to the host's loopback:

```bash
docker run --rm -p 127.0.0.1:8000:8000 ru-marketplace-mcp:1.1.0
# -> http://127.0.0.1:8000/mcp on the host
```

Run a different marketplace by overriding the command:

```bash
docker run --rm -p 127.0.0.1:8001:8000 ru-marketplace-mcp:1.1.0 yandex-mcp
```

`-p 127.0.0.1:8000:8000` is the security boundary. `-p 8000:8000` would publish
on all host interfaces and put the unauthenticated server on your LAN; do not.

### Compose

`docker-compose.yml` runs several servers at once — the same image with a
different command each, each on its own host port, every port published to
`127.0.0.1` only:

```bash
docker compose up -d          # wb:8000 yandex:8001 detmir:8002 ozon:8003 compare:8004
docker compose logs -f wb
docker compose down
```

## Honest limitations

### Ozon tier 2 (authenticated Chrome) does not work in Docker by default

Ozon has two fetch tiers (see [CDP_SETUP.md](CDP_SETUP.md)). Tier 2 exists
because Ozon answers datacenter traffic with an endless redirect loop that no
TLS fingerprint clears; the fix is to fetch **inside a browser you are already
logged into**, over the Chrome DevTools Protocol.

The connector's CDP client dials `http://127.0.0.1:<CHROME_CDP_PORT>`, and **the
host is hardcoded to `127.0.0.1`** — only the port is configurable, via
`CHROME_CDP_PORT`. Inside a container, `127.0.0.1` is the container itself, where
no Chrome is running. So tier 2 is dead in a stock container, and tools that
depend on it report Ozon as unavailable rather than returning wrong data.

The options, honestly:

- **Host networking (`network_mode: host`, Linux only).** The container shares
  the host's network namespace, so the host's `127.0.0.1:9222` becomes reachable
  and the hardcoded dial target resolves. This is the only clean route given the
  hardcoded host. It drops the container's port isolation (host networking
  ignores `ports:`), so set `MCP_HTTP_HOST=127.0.0.1` to keep the MCP endpoint on
  the host's loopback. The commented `ozon` variant in `docker-compose.yml` shows
  this.
- **`host.docker.internal` does not help.** It resolves the host from inside a
  container, but the CDP client never dials it — it only dials `127.0.0.1`. There
  is no `CHROME_CDP_HOST` variable to point elsewhere.
- **Run Chrome for the CDP tier on the host, not in the container.** Chrome's
  sandbox will not run as root and a headless browser is easy to fingerprint;
  keeping the logged-in browser on your own machine is also what bounds the risk.

Whichever you pick, understand the trade: a reachable CDP debug port grants **full
control of that Chrome profile and every session in it**. Use a dedicated
scraping profile logged into Ozon and nothing else, exactly as CDP_SETUP.md
requires. Never expose 9222 beyond loopback.

### Ozon tier 1 (and any Russian marketplace) needs a Russian-friendly IP

Tier 1 is anonymous HTTP, but Ozon commonly refuses non-Russian and datacenter
addresses outright. A container on a cloud host will usually be blocked, so Ozon
tools return "unavailable" there regardless of transport. Route through a Russian
**residential** proxy to change that: set `OZON_PROXY`, or the standard
`HTTPS_PROXY`/`ALL_PROXY`. The same geo reality applies in spirit to the other
marketplaces — a datacenter IP is a worse vantage point than a residential
Russian one — though Wildberries, Yandex Market, and Detsky Mir tolerate it far
better than Ozon does. `compare_prices` degrades gracefully: a blocked source is
reported as blocked (`complete: false`) and the rest are still ranked.
