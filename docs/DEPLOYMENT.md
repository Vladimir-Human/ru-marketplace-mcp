# Deployment

Two ways to run the connectors. Both are optional; neither changes the default.

- **stdio** (default): the MCP client spawns the server as a subprocess and
  talks over stdin/stdout. This is what every client config in the README does,
  and it is unchanged — if you do nothing here, nothing about your setup moves.
- **HTTP** (opt-in): the server listens on a port and speaks MCP over
  streamable HTTP, for running it remotely or in a container. You turn it on
  with `MCP_TRANSPORT=http` and configure authentication for network binds.

HTTP servers support bearer authentication and a static tenant header. Binding
beyond loopback requires both credentials at startup, including inside Docker.
Each process and its browser profile serve one tenant.

## Transport selection

Selection is environment-driven and lives in `mcp_core.runtime`, shared by all
connector entry points, including the unified `marketplace-mcp`,
so they behave identically.

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`, `http`, `streamable-http`, or `sse`. `http` is the modern streamable-HTTP transport; the other two are FastMCP's own aliases, kept for operators who know them. |
| `MCP_HTTP_HOST` | `127.0.0.1` | Bind address. HTTP only. Loopback by default — see below. |
| `MCP_HTTP_PORT` | `8000` | Bind port. HTTP only. |
| `MCP_HTTP_PATH` | `/mcp` | Endpoint path. HTTP only. |
| `MCP_HTTP_AUTH_TOKEN` | unset | Bearer token. Required for non-loopback HTTP binds. |
| `MCP_HTTP_TENANT_ID` | unset | Required for non-loopback binds. With bearer auth enabled, matched against `X-MCP-Tenant`; use one process/profile per tenant. |

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

## Unified source selection

The unified `marketplace-mcp` server mounts every source by default. Set
`MARKETPLACE_SOURCES` to a comma-separated list of canonical names to reduce the
tool surface and wire cost, for example
`wildberries,ozon,yandex_market,compare`. The aliases `wb`, `ym`/`yandex`,
`detmir`, and `ali` are accepted. Unset or blank means all sources; unknown
names fail at startup with the supported-source list instead of silently
starting a partial server. Deselected sources remain visible in
`marketplace_sources.skipped` with a `deselected` reason, and `compare_prices`
uses the same selected subset.

CI also checks the model-facing wire cost against the committed
`work/performance/wire-baseline.json` snapshot. The gate allows at most 10%
growth per profile; update the snapshot deliberately when a tool schema change
is intended and review the resulting diff.

### stdio (default, unchanged)

Nothing to configure. The release workflow attaches wheels and sdists to each
GitHub Release; it does not publish these package names to PyPI. Use a checkout
when you want the complete workspace with one frozen lockfile:

```console
uv sync --frozen --all-packages
uv run --frozen --directory /path/to/ru-marketplace-mcp compare-mcp
```

For a standalone release install, download the matching-version wheels from
the [GitHub Release](https://github.com/Vladimir-Human/ru-marketplace-mcp/releases)
into one `wheelhouse` directory. The unified wheel and every connector wheel
are separate artifacts, so download all workspace wheels when using
`marketplace-mcp`; `compare-connector[all]` needs `mcp-core`, WB, Detsky Mir,
Yandex, and its optional source wheels. Install from that directory so the
project's pinned mcp-core dependency resolves to the matching project wheel
instead of an unrelated public package. Since v2.4.2 the `all` extra already
includes AliExpress; the explicit AliExpress pin below is redundant there but keeps
the same command working with the v2.4.1 wheels, whose extra predates it — swap
the version to match the release you downloaded.

```console
python -m venv .venv
.venv/bin/python -m pip install --no-deps wheelhouse/mcp_core-2.7.0-py3-none-any.whl
.venv/bin/python -m pip install --find-links wheelhouse "compare-connector[all]==2.7.0" "aliexpress-connector==2.7.0"
.venv/bin/compare-mcp
```

Installing the matching `mcp-core` wheel first with `--no-deps` prevents pip
from selecting the unrelated public package with the same distribution name;
the second command then resolves its ordinary third-party dependencies from
the package index and all workspace dependencies from `wheelhouse`.
On Windows, use `.venv\Scripts\python.exe` and
`.venv\Scripts\compare-mcp.exe` (and replace the `.venv/bin/python`
prefixes above accordingly).
The wheelhouse must contain the same release version for every downloaded
workspace package; do not mix versions. In a source checkout, the corrected
metadata includes `aliexpress-connector` in `compare-connector[all]`; Cian and
MPStats are unified-server sources, not comparison sources, and are intentionally
not part of that extra.

## PyPI (maintainer)

The additional bundle is one distribution, `ru-marketplace-mcp`; it contains all
workspace import packages and console scripts, including the `ru-marketplace-mcp`
alias of `marketplace-mcp`. The workspace, its per-package GitHub Release wheels
and sdists, and the Docker builds remain separate and unchanged. Do not publish
`mcp-core` or the generic connector names to PyPI.

Publishing is **on** in this repository since 2026-10-31: the owner registered
the publisher, limited the `pypi` environment to tags `v*` and set the variable,
and v2.7.0 went out by a manual dispatch on its tag. Every new `v*` tag now
publishes the bundle. Nothing in this checkout registers a publisher or uploads
a package by itself. The one-time owner setup, kept for reference and for forks:

1. On **pypi.org**, open **Account → Publishing → Add a new pending publisher**.
   Enter these exact values:

   | Field | Value |
   |---|---|
   | PyPI project name | `ru-marketplace-mcp` |
   | Owner | `Vladimir-Human` |
   | Repository | `ru-marketplace-mcp` |
   | Workflow | `pypi-publish.yml` |
   | Environment | `pypi` |

2. In the GitHub repository, open **Settings → Environments → New environment**
   and create `pypi` (add release approval rules if desired).
3. Open **Settings → Secrets and variables → Actions → Variables** and add the
   repository variable `PYPI_PUBLISH` with value `true`, only after registration
   is complete. There is **no API token** to create or store.

Until step 3, the whole publish job is skipped. Removing the variable or setting
it to anything other than `true` disables publishing again. A `v*` tag triggers
`.github/workflows/pypi-publish.yml`; a manual dispatch must select a `v*` tag,
not a branch. Before upload, the job reads the built wheel's `METADATA` and checks
that its version equals that tag. Trusted publishing uses the `pypi` environment
and an OIDC identity with `id-token: write` and `contents: read` only.

For a local preview/build from a synced development checkout:

```console
uv run python scripts/build_pypi_bundle.py --print-pyproject
uv run python scripts/build_pypi_bundle.py --out dist/pypi
```

The builder derives the root release metadata, all member third-party
requirements (including optional transport dependencies), and scripts directly
from manifests. Workspace dependencies are removed; conflicting third-party
specifiers or script names fail with their sources, rather than being reconciled
silently. Existing transport declarations use the higher minimums already
required by members. The temporary project includes every `py.typed`, the README
and license; the sdist contains its own sources and can be rebuilt without the
checkout. `dist/pypi/` is ignored. CI's additional **PyPI bundle installs** job
installs the wheel with plain pip into a fresh non-workspace venv and runs both
CLI help commands and the offline stdio probe against installed console scripts.

A client configured from a uv tool install (`uvx ru-marketplace-mcp setup`) must not keep a path into
uv's deletable cache. When `UV` is set and the running prefix lies inside
`UV_CACHE_DIR` (or `uv cache dir`), `install` and `setup` write the absolute uv
binary plus `tool run --from ru-marketplace-mcp==<installed-version> <script>`.
That pin is reproducible. To move to a newer release, run that release's
`marketplace-mcp install <client> --write --force`; the CLI note shows the
version-selection command. `<client>` is `claude`, `cursor` or `claude-code`.
This is preparation for the owner's first PyPI release, not a claim that the
package is already available there.

## HTTP (opt-in)

Set these once in the shell that starts the server or Docker. The token command
generates a secret locally without printing it. Configure the same values in
your MCP client; keep the token out of source control and shared logs.

```bash
export MCP_HTTP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MCP_HTTP_TENANT_ID=marketplace-local
MCP_TRANSPORT=http MCP_HTTP_HOST=127.0.0.1 MCP_HTTP_PORT=8000 wb-mcp
```

The endpoint is `http://127.0.0.1:8000/mcp`. Configure the client to send these
headers on its MCP requests, substituting the actual environment values:

```http
Authorization: Bearer <value of MCP_HTTP_AUTH_TOKEN>
X-MCP-Tenant: <value of MCP_HTTP_TENANT_ID>
```

A successful MCP initialize returns the server info. A bare `GET /mcp` is not
a health check: it does not perform the MCP initialization/session exchange.

## Security posture

**The default bind host is `127.0.0.1`.** Loopback can run without auth for local
client compatibility; setting `MCP_HTTP_AUTH_TOKEN` enables bearer checking
there too. A non-loopback bind refuses to start unless both
`MCP_HTTP_AUTH_TOKEN` and `MCP_HTTP_TENANT_ID` are nonblank. Authenticated MCP
requests must match the configured bearer token and tenant id.

The tenant id is an access check, not a namespace for per-user state. Callers
with the same credentials share connector state and the configured Chrome
profile. Deploy a separate process/container, credentials, browser profile,
profile volume, and CDP endpoint for each tenant. A shared Chrome sidecar must
only be used by services belonging to that same tenant.

Successful non-loopback startup still logs `http_bind_exposed` to stderr.
For remote clients, use a TLS reverse proxy and rate limits, and keep the MCP
port on loopback or a private interface reachable only by that proxy. Forward
both client headers unchanged; the built-in checks do not provide TLS or
per-tenant browser isolation.

## Docker

A multi-stage `Dockerfile` and a `docker-compose.yml` are included. The image
installs the workspace with uv into a virtualenv in a builder stage, then copies
only that virtualenv and the source into a plain `python:3.12-slim-trixie`
runtime — uv does not ship in the runtime image. It runs as a **non-root** user,
because a network-reachable scraper has no business being root in its container.

Image tags are pinned, each with its digest: the builder is a
`ghcr.io/astral-sh/uv:<version>-python3.12-trixie-slim` image and the runtime is
`python:3.12-slim-trixie`. Dependabot moves the uv tag and both digests weekly;
the runtime stays on Python 3.12 until that is changed on purpose.

### Build

```bash
docker build -t ru-marketplace-mcp:2.7.0 .
```

The install uses `uv sync --all-packages --frozen`: `--all-packages` installs
every workspace member, `--frozen` uses `uv.lock` exactly and never re-resolves,
so the image's dependency set matches local development bit for bit.

### Run one server

The root `Dockerfile` runs HTTP for detached containers. For a client that speaks
over stdin/stdout, build `Dockerfile.stdio` and run it with `docker run --rm -i`
instead.

The image defaults to `MCP_TRANSPORT=http` and, **inside the container**,
`MCP_HTTP_HOST=0.0.0.0`. That is deliberate and is not a contradiction of the
loopback rule: inside the container `127.0.0.1` would be unreachable from the
host, so the container binds to all of its *own* interfaces and the perimeter
moves to the **published port**. The non-loopback auth requirements still
apply. Set the two environment variables as shown above, pass them into the
container, and publish the port to the host's loopback:

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  -e MCP_HTTP_AUTH_TOKEN -e MCP_HTTP_TENANT_ID ru-marketplace-mcp:2.7.0
# -> http://127.0.0.1:8000/mcp on the host
```

Run a different marketplace by overriding the command:

```bash
docker run --rm -p 127.0.0.1:8001:8000 \
  -e MCP_HTTP_AUTH_TOKEN -e MCP_HTTP_TENANT_ID ru-marketplace-mcp:2.7.0 yandex-mcp
```

`-p 127.0.0.1:8000:8000` keeps access local in addition to the MCP auth checks.
`-p 8000:8000` publishes on all host interfaces and exposes an unencrypted
endpoint on your LAN. Keep the loopback binding and use a TLS proxy for remote
access.

### Compose

`docker-compose.yml` runs several servers at once — the same image with a
different command each, each on its own host port, every port published to
`127.0.0.1` only. Every service inherits the required token and tenant id from
the shell environment above (or a protected local `.env` file). Compose rejects
missing or empty values before starting containers. This stack serves one
tenant; separate tenants need separate stacks and Chrome profiles.

```bash
docker compose up -d          # wb:8000 yandex:8001 detmir:8002 ozon:8003 compare:8004 … mpstats:8012
docker compose logs -f wb
docker compose down
```

Use the same `Authorization` and `X-MCP-Tenant` headers shown above for each
service. `docker compose config --quiet` validates configuration without
printing the expanded secret values.

The `mpstats` service is the optional paid source: it starts without
`MPSTATS_MP_AUTH`, but its tools answer `auth_missing` until you set the token
in its `environment:` block. Note the same extends to `marketplace-mcp doctor`:
without the token the mpstats selfcheck reports `inconclusive`, so doctor exits
`2` even when everything else is healthy — the same semantics the CDP sources
have without a Chrome. Run `marketplace-mcp doctor wb ozon ...` (named sources)
to skip it, exactly as you would skip a CDP source you do not use.

## Honest limitations

### Tier-2 sources (authenticated Chrome) need a reachable Chrome in Docker

Ozon, Avito, Taobao, Megamarket, Lamoda search, DNS and Citilink all have a
tier 2 (see [CDP_SETUP.md](CDP_SETUP.md)). Tier 2 exists
because Ozon answers datacenter traffic with an endless redirect loop that no
TLS fingerprint clears; the fix is to fetch **inside a browser you are already
logged into**, over the Chrome DevTools Protocol.

The connector's CDP client dials `http://<CHROME_CDP_HOST>:<CHROME_CDP_PORT>`.
Both are configurable: `CHROME_CDP_HOST` defaults to `127.0.0.1` and
`CHROME_CDP_PORT` to `9222`. Inside a container, `127.0.0.1` is the container
itself, where no Chrome is running — so a stock container cannot reach the
host's browser until you point the host elsewhere.

The options, honestly:

- **Chrome sidecar (cleanest in Docker).** Run Chrome with remote debugging in
  a second container on the same compose network and set
  `CHROME_CDP_HOST=chrome` (the service name). The CDP client dials the sidecar,
  port isolation stays intact, and no host networking is needed. The scraping
  profile lives in a named volume, so your Ozon login survives rebuilds.
- **`host.docker.internal` (Desktop and modern Linux).** Points at the host's
  browser from inside the container: `CHROME_CDP_HOST=host.docker.internal`.
  On Linux this needs Docker 20.10+ with `--add-host=host.docker.internal:host-gateway`,
  which recent Docker and compose add automatically.
- **Host networking (`network_mode: host`, Linux only).** The legacy route:
  the container shares the host's network namespace, so the host's
  `127.0.0.1:9222` resolves. It drops port isolation (host networking ignores
  `ports:`), so set `MCP_HTTP_HOST=127.0.0.1` to keep the MCP endpoint on the
  host's loopback. The commented `ozon` variant in `docker-compose.yml` still
  shows this for hosts where the other two are unavailable.
- **Run Chrome for the CDP tier on the host, not in the container.** Chrome's
  sandbox will not run as root and a headless browser is easy to fingerprint;
  keeping the logged-in browser on your own machine is also what bounds the
  risk. When the client dials a remote host it never tries to autostart Chrome
  locally — autostart is loopback-only, because a remote host means you run
  that browser yourself.

Whichever you pick, understand the trade: a reachable CDP debug port grants **full
control of that Chrome profile and every session in it**. Use a dedicated
scraping profile logged into the marketplaces you need and nothing else, exactly
as CDP_SETUP.md requires. Never expose 9222 beyond loopback.

The same tier-2 story now covers every challenge-gated source, not just Ozon:
Avito (IP firewall), Taobao (signed mtop API), Megamarket (ServicePipe), Lamoda
search (redirect loop), DNS and Citilink (Qrator proof-of-work). One Chrome
sidecar serves them all — log each marketplace into the same dedicated profile
once, and `CHROME_CDP_HOST=chrome` lets every connector reach it.

### Any Russian marketplace needs a Russian-friendly IP

Tier 1 is anonymous HTTP, but Ozon, Avito and others commonly refuse non-Russian
and datacenter addresses outright. A container on a cloud host will usually be
blocked, so those tools return "unavailable" there regardless of transport.
Route through a Russian **residential** proxy to change that: set the per-source
`*_PROXY`, or the standard `HTTPS_PROXY`/`ALL_PROXY`. The same geo reality
applies in spirit to every source — a datacenter IP is a worse vantage point
than a residential Russian one — though Wildberries, Yandex Market, and Detsky
Mir tolerate it far better than Ozon or Avito do. `compare_prices` degrades
gracefully: a blocked source is reported as blocked (`complete: false`) and the
rest are still ranked.
