# syntax=docker/dockerfile:1

# Multi-stage build for the ru-marketplace-mcp connectors.
#
# Stage 1 installs the workspace with uv into a self-contained virtualenv.
# Stage 2 is a plain Python runtime that copies only that virtualenv plus the
# source — uv itself never ships in the runtime image, keeping it lean and
# reducing the attack surface. This is the layout uv's own Docker guide
# recommends.
#
# Tags are pinned deliberately. The builder pins uv 0.11.32 (the version this
# repo's uv.lock was produced with) on Python 3.12; the runtime pins the same
# Python minor. Floating tags would let a base rebuild change the toolchain out
# from under a reproducible install.

# ---- Stage 1: build the environment -----------------------------------------
FROM ghcr.io/astral-sh/uv:0.11.32-python3.12-trixie-slim AS builder

# - bytecode compile for faster cold starts (production images start often).
# - copy mode, not hardlink: the cache mount and the image are different
#   filesystems, so hardlinks would warn and fall back anyway.
# - install into the project's own .venv at /app/.venv.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# First install ONLY dependencies, keyed on the manifests and lockfile. This
# layer is cached and reused across source edits, so day-to-day rebuilds skip
# re-downloading the dependency set. --frozen means "use uv.lock exactly, never
# re-resolve"; --no-install-project defers the workspace packages themselves to
# the next step; --no-dev drops the test/lint toolchain from the runtime image.
#
# Every one of the 13 workspace members must be listed here. `uv sync
# --all-packages` reads each member's pyproject.toml even under
# --no-install-project (it still resolves their workspace deps), so a missing
# manifest fails the resolve with "Distribution not found at:
# file:///app/packages/<member>". A `COPY packages/*/pyproject.toml ...` glob
# does NOT work: a multi-file COPY flattens every match into one destination
# path, collapsing the packages/<member>/ nesting the file:// sources require.
# So the members are enumerated explicitly, one COPY each.
COPY pyproject.toml uv.lock ./
COPY packages/mcp-core/pyproject.toml packages/mcp-core/pyproject.toml
COPY packages/wb-connector/pyproject.toml packages/wb-connector/pyproject.toml
COPY packages/ozon-connector/pyproject.toml packages/ozon-connector/pyproject.toml
COPY packages/yandex-connector/pyproject.toml packages/yandex-connector/pyproject.toml
COPY packages/detmir-connector/pyproject.toml packages/detmir-connector/pyproject.toml
COPY packages/compare-connector/pyproject.toml packages/compare-connector/pyproject.toml
COPY packages/avito-connector/pyproject.toml packages/avito-connector/pyproject.toml
COPY packages/taobao-connector/pyproject.toml packages/taobao-connector/pyproject.toml
COPY packages/megamarket-connector/pyproject.toml packages/megamarket-connector/pyproject.toml
COPY packages/lamoda-connector/pyproject.toml packages/lamoda-connector/pyproject.toml
COPY packages/dns-connector/pyproject.toml packages/dns-connector/pyproject.toml
COPY packages/citilink-connector/pyproject.toml packages/citilink-connector/pyproject.toml
COPY packages/marketplace-connector/pyproject.toml packages/marketplace-connector/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --frozen --no-dev --no-install-project

# Now copy the source and install the workspace packages themselves. Only this
# thin layer rebuilds when application code changes.
COPY packages/ packages/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --frozen --no-dev

# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim-trixie AS runtime

# Run as an unprivileged user: a scraper reachable over HTTP should not be root
# inside its container. Create the user first so the copied tree is owned by it.
RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app app

WORKDIR /app

# Copy the built virtualenv and the source it points at. The .venv is
# relocatable here because UV_PROJECT_ENVIRONMENT put it at the same /app/.venv
# path in the builder. Ownership is handed to the non-root user.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/packages /app/packages

# The skills travel with the servers. Each connector has one, and it is how an
# agent learns the tool exists, when to reach for it, and which of its answers
# need a second look. A container with the servers but not the skills runs
# twelve MCP endpoints nothing knows how to use.
COPY --chown=app:app skills/ /app/skills/

# Put the venv on PATH so the console scripts (wb-mcp, ozon-mcp, ...) resolve
# directly, and keep Python from writing .pyc files or buffering stdout/stderr.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# HTTP transport is the only mode that makes sense in a detached container:
# stdio needs a client attached to this process's stdin/stdout, which nothing
# is. Bind to 0.0.0.0 so the port is reachable from OUTSIDE the container —
# the container boundary plus a locked-down published port is the perimeter
# here, NOT the in-process bind host. Publish this to 127.0.0.1 on the host
# (see docker-compose.yml) and front it with an authenticating reverse proxy
# before exposing it anywhere: these servers have no auth of their own.
ENV MCP_TRANSPORT=http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000 \
    MCP_HTTP_PATH=/mcp

USER app

EXPOSE 8000

# No HEALTHCHECK on purpose. These servers expose only the FastMCP JSON-RPC
# endpoint at $MCP_HTTP_PATH (/mcp); there is no plain-GET liveness route in the
# codebase (mcp_core/http.py is httpx timeout/error helpers, and runtime.py hands
# the socket straight to FastMCP without registering a custom route). The /mcp
# endpoint speaks streamable-HTTP: a bare GET does not return a simple 200, so a
# naive `curl -f http://127.0.0.1:8000/mcp` would flap. A truthful healthcheck
# needs a real liveness route — add `@mcp.custom_route("/healthz", methods=["GET"])`
# in mcp-core returning 200, then a HEALTHCHECK hitting it — but that is a code
# change in packages/, out of scope here. Until then, no check beats a lying one.

# Default to the Wildberries server; override the command to run any of the
# other twelve entry points — ozon-mcp, yandex-mcp, detmir-mcp, compare-mcp,
# avito-mcp, taobao-mcp, megamarket-mcp, lamoda-mcp, dns-mcp, citilink-mcp, or
# the unified marketplace-mcp (all sources in one server). docker-compose.yml
# shows running several at once, each on its own published port.
CMD ["wb-mcp"]
