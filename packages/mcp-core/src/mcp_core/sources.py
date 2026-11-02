"""One registry of the marketplace sources this release ships.

The fact "the project knows about marketplace X" used to live in about fifteen
places — the unified mount table, the capability table, the per-source CLI
config keys, the selfcheck table, the unblock page map, compare's card-tool and
searchable lists, the drift-probe script, the stdio end-to-end expected counts,
the workspace manifests and the CI tables. Every one of them had to be
remembered when a source was added, and the audit of 2026-11-01 found they had
already drifted apart (CI listed a source its own import table did not).

This module is that fact, once, as plain data: one ``SourceSpec`` per server
this repository ships. It imports nothing from a connector, so a caller can
read it without mounting anything — that is the reason it lives in mcp-core
rather than next to the unified server (see ``capabilities.py``, which needs the
same freedom for the operator CLI). The derived tables below are what the
callers read; a caller that needs a list this module does not derive should add
it here rather than copy the facts out again.

Not every site can import Python. ``pyproject.toml``, ``.github/workflows/ci.yml``
and ``server.json`` are data for other tools; ``marketplace_connector/server.py``'s
``mounts`` table, ``compare_connector/server.py``'s ``SEARCHABLE`` and
``scripts/diagnose_drift.py``'s ``SOURCES`` are parsed out of their source by
existing tests. Those sites stay literal, and ``test_source_registry_parity.py``
pins each of them to this registry so a drift fails the suite instead of
shipping.
"""
# START_MODULE_CONTRACT
# PURPOSE: Declare every shipped source once and derive the tables its callers read.
# SCOPE: Static data (names, packages, scripts, access metadata, URLs, counts) plus the
#        configuration-aware route of a source; no imports of connectors.
# DEPENDS: stdlib dataclasses,os,typing.
# LINKS: M-SOURCES; M-SOURCE-SELECTION; M-CAPABILITIES; M-UNBLOCK; M-MARKETPLACE; M-COMPARE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# SourceSpec: One shipped server's identity, wiring and access facts.
# SOURCES: The registry, in unified mount order.
# BY_NAME: Registry lookup by canonical source name.
# MOUNTS: (mount name, module path) rows the unified server mounts.
# MOUNTABLE: Canonical names MARKETPLACE_SOURCES may select.
# CAPABILITIES: Per-source access tier, CDP/login needs, currency and text search.
# source_route: The route a source takes now, following its configuration.
# wb_route: The route WB takes with a given transport and fallback setting.
# ALIASES: Accepted spellings mapped to canonical source names.
# SELFCHECKS: (name, module path, selfcheck function) rows the operator CLI runs.
# CONFIG_SERVERS: (config key, console script, note) rows the per-source client config uses.
# UNBLOCK_PAGES: Browser-backed source -> page a human opens to pass its check.
# LOGIN_UNBLOCK_SOURCES: Sources whose wall is a login rather than a bot check.
# DRIFT_PROBE_SOURCES: Sources scripts/diagnose_drift.py can probe.
# COMPARE_SOURCES: Sources compare_prices searches, in registry order.
# CARD_TOOL_NAMES: Source -> card tool compare uses to inspect one offer.
# FOREIGN_CURRENCY_SOURCES: Compare sources that do not quote roubles.
# MOUNT_GATED_SOURCES: Sources the unified server mounts only when configured.
# END_MODULE_MAP

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ALIASES",
    "BY_NAME",
    "CAPABILITIES",
    "CARD_TOOL_NAMES",
    "COMPARE_SOURCES",
    "CONFIG_SERVERS",
    "DRIFT_PROBE_SOURCES",
    "FOREIGN_CURRENCY_SOURCES",
    "LOGIN_UNBLOCK_SOURCES",
    "MOUNTABLE",
    "MOUNTS",
    "MOUNT_GATED_SOURCES",
    "SELFCHECKS",
    "SOURCES",
    "UNBLOCK_PAGES",
    "SourceSpec",
    "source_route",
    "wb_route",
]


@dataclass(frozen=True)
class SourceSpec:
    """What the project knows about one shipped server.

    ``name`` is the canonical vocabulary: it is what ``MARKETPLACE_SOURCES``
    accepts (through ``aliases``), what compare reports as a source, what
    ``doctor`` prints and what ``UNBLOCK_PAGES`` is keyed by. The unified server
    mounts under ``mount`` instead, because its table predates that vocabulary
    (``yandex``, ``detmir``) and renaming it would rename mounted tools.

    Facts that only one caller needs still live here, because the point of the
    registry is that adding the seventeenth source is one record rather than
    seventeen edits: ``tools`` is the model-facing tool count the stdio check
    and the CI inventory assert, ``note`` is the one line the per-source client
    config prints, ``drift_probe`` says whether ``scripts/diagnose_drift.py``
    can probe it.
    """

    name: str
    package: str
    module: str
    console_script: str
    # The prefix every tool of this server carries. It is the name its tools
    # were named in, which for three sources is neither the canonical name nor
    # the mount name (wb_, detmir_, yandex_), so it is recorded, not derived.
    tool_prefix: str = ""
    aliases: tuple[str, ...] = ()
    kind: str = "marketplace"
    # Unified mount name; None means this server is not mounted as a source.
    mount: str | None = None
    # Per-source client config key (`marketplace-mcp install --per-source`).
    config_key: str | None = None
    selfcheck: str | None = None
    card_tool: str | None = None
    tools: int = 0
    # Access metadata, present for kind="marketplace" only.
    access: str = ""
    # The browser is unavoidable for this source: it cannot answer without it.
    requires_cdp: bool = False

    requires_login: bool = False
    currency: str = ""
    text_search: bool = False
    unblock_page: str | None = None
    login_wall: bool = False
    drift_probe: bool = False
    # Mounted only when its own credential is configured (R-MPSTATS-MOUNT-GATE).
    mount_gated: bool = False
    note: str = ""


# Registry order is unified mount order: the operator-facing tables derived
# below keep the order their callers already printed, and a new source is added
# here in the position its tools should appear.
SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        name="wildberries",
        aliases=("wb",),
        package="wb-connector",
        module="wb_connector.server",
        console_script="wb-mcp",
        tool_prefix="wb_",
        mount="wildberries",
        config_key="wildberries",
        selfcheck="wb_selfcheck",
        card_tool="wb_card",
        tools=8,
        access="anonymous_http",
        currency="rub",
        text_search=True,
        # WB's route is CONFIGURABLE (WB_TRANSPORT / WB_DOM_FALLBACK): the
        # browser is not part of its static tier, so the inventory follows the
        # mode instead of this column (source_route).
        note="anonymous HTTP — availability depends on your network",
    ),
    SourceSpec(
        name="ozon",
        package="ozon-connector",
        module="ozon_connector.server",
        console_script="ozon-mcp",
        tool_prefix="ozon_",
        mount="ozon",
        config_key="ozon",
        selfcheck="ozon_selfcheck",
        card_tool="ozon_card",
        tools=3,
        access="http_or_cdp",
        currency="rub",
        text_search=True,
        # The browser tier opens the site root and fetches the search JSON from
        # inside that page (ozon_connector/server.py).
        unblock_page="https://www.ozon.ru/",
        note="needs your Chrome for tier 2 — see docs/CDP_SETUP.md",
    ),
    SourceSpec(
        name="yandex_market",
        aliases=("yandex", "ym"),
        package="yandex-connector",
        module="yandex_connector.server",
        console_script="yandex-mcp",
        tool_prefix="yandex_",
        mount="yandex",
        config_key="yandex-market",
        selfcheck="yandex_selfcheck",
        card_tool="yandex_card",
        tools=2,
        access="anonymous_http",
        currency="rub",
        text_search=True,
        note="anonymous HTTP",
    ),
    SourceSpec(
        name="detsky_mir",
        aliases=("detmir",),
        package="detmir-connector",
        module="detmir_connector.server",
        console_script="detmir-mcp",
        tool_prefix="detmir_",
        mount="detmir",
        config_key="detsky-mir",
        selfcheck="detmir_selfcheck",
        card_tool="detmir_card",
        tools=3,
        access="anonymous_http",
        currency="rub",
        text_search=False,
        note="anonymous HTTP",
    ),
    SourceSpec(
        name="avito",
        package="avito-connector",
        module="avito_connector.server",
        console_script="avito-mcp",
        tool_prefix="avito_",
        mount="avito",
        config_key="avito",
        selfcheck="avito_selfcheck",
        card_tool="avito_card",
        tools=3,
        access="cdp",
        requires_cdp=True,
        currency="rub",
        text_search=True,
        # The items API is fetched from a page opened at the root.
        unblock_page="https://www.avito.ru/",
        note="needs your Chrome — IP firewall",
    ),
    SourceSpec(
        name="taobao",
        package="taobao-connector",
        module="taobao_connector.server",
        console_script="taobao-mcp",
        tool_prefix="taobao_",
        mount="taobao",
        config_key="taobao",
        selfcheck="taobao_selfcheck",
        card_tool="taobao_card",
        tools=2,
        access="cdp",
        requires_cdp=True,
        requires_login=True,
        # Yuan sources rank separately from ruble ones: a baked-in CNY→RUB rate
        # would go silently stale, and ranking a stale conversion against live
        # ruble prices fabricates bargains (compare.FOREIGN_CURRENCY_SOURCES).
        currency="cny",
        text_search=True,
        # The wall is a login, so the human goes straight to the login page.
        unblock_page="https://login.taobao.com/member/login.jhtml",
        login_wall=True,
        drift_probe=True,
        note="needs your Chrome — signed API",
    ),
    SourceSpec(
        name="megamarket",
        package="megamarket-connector",
        module="megamarket_connector.server",
        console_script="megamarket-mcp",
        tool_prefix="megamarket_",
        mount="megamarket",
        config_key="megamarket",
        selfcheck="megamarket_selfcheck",
        card_tool="megamarket_card",
        tools=2,
        access="cdp",
        requires_cdp=True,
        requires_login=True,
        currency="rub",
        text_search=True,
        # The wall is an unauthenticated session; the home page carries the
        # login entry point.
        unblock_page="https://megamarket.ru/",
        login_wall=True,
        drift_probe=True,
        note="needs your Chrome — ServicePipe",
    ),
    SourceSpec(
        name="lamoda",
        package="lamoda-connector",
        module="lamoda_connector.server",
        console_script="lamoda-mcp",
        tool_prefix="lamoda_",
        mount="lamoda",
        config_key="lamoda",
        selfcheck="lamoda_selfcheck",
        card_tool="lamoda_card",
        tools=2,
        access="graphql_or_cdp",
        currency="rub",
        text_search=True,
        # Search is rendered in Chrome at this URL.
        unblock_page="https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
        drift_probe=True,
        note="cards anonymous; search needs your Chrome",
    ),
    SourceSpec(
        name="dns",
        package="dns-connector",
        module="dns_connector.server",
        console_script="dns-mcp",
        tool_prefix="dns_",
        mount="dns",
        config_key="dns",
        selfcheck="dns_selfcheck",
        card_tool="dns_card",
        tools=2,
        access="cdp",
        requires_cdp=True,
        currency="rub",
        text_search=True,
        # The selfcheck renders this search URL.
        unblock_page="https://www.dns-shop.ru/search/?q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA",
        drift_probe=True,
        note="needs your Chrome — Qrator proof-of-work",
    ),
    SourceSpec(
        name="citilink",
        package="citilink-connector",
        module="citilink_connector.server",
        console_script="citilink-mcp",
        tool_prefix="citilink_",
        mount="citilink",
        config_key="citilink",
        selfcheck="citilink_selfcheck",
        card_tool="citilink_card",
        tools=2,
        access="cdp",
        requires_cdp=True,
        currency="rub",
        text_search=True,
        # The selfcheck renders this search URL.
        unblock_page="https://www.citilink.ru/search/?text=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA",
        drift_probe=True,
        note="needs your Chrome — Qrator",
    ),
    SourceSpec(
        name="aliexpress",
        aliases=("ali",),
        package="aliexpress-connector",
        module="aliexpress_connector.server",
        console_script="aliexpress-mcp",
        tool_prefix="aliexpress_",
        mount="aliexpress",
        config_key="aliexpress",
        selfcheck="aliexpress_selfcheck",
        card_tool="aliexpress_card",
        tools=2,
        access="cdp",
        requires_cdp=True,
        currency="rub",
        text_search=True,
        # The selfcheck renders this landing page.
        unblock_page="https://aliexpress.ru/wholesale?SearchText=%D1%87%D0%B0%D1%81%D1%8B",
        note="needs your Chrome — AliExpress Russia",
    ),
    SourceSpec(
        name="cian",
        package="cian-connector",
        module="cian_connector.server",
        console_script="cian-mcp",
        tool_prefix="cian_",
        mount="cian",
        config_key="cian",
        selfcheck="cian_selfcheck",
        tools=2,
        access="cdp",
        requires_cdp=True,
        currency="rub",
        # Real estate, not goods: search is by filters (deal, type, region,
        # rooms, price, area), never by text, and it takes no part in
        # compare_prices.
        text_search=False,
        # Search POSTs to api.cian.ru from a page opened at the root.
        unblock_page="https://www.cian.ru/",
        note="real estate; needs your Chrome — WAF by IP",
    ),
    SourceSpec(
        name="compare",
        kind="profile",
        package="compare-connector",
        module="compare_connector.server",
        console_script="compare-mcp",
        tool_prefix="compare_",
        mount="compare",
        config_key="compare-prices",
        tools=4,
        note="fans out across all of the above",
    ),
    SourceSpec(
        name="mpstats",
        package="mpstats-connector",
        module="mpstats_connector.server",
        console_script="mpstats-mcp",
        tool_prefix="mpstats_",
        mount="mpstats",
        config_key="mpstats",
        selfcheck="mpstats_selfcheck",
        tools=2,
        access="api_token",
        currency="rub",
        text_search=False,
        mount_gated=True,
        note="optional paid analytics — requires MPSTATS_MP_AUTH in the server environment",
    ),
    # The project's own faces: no marketplace of their own, not selectable by
    # MARKETPLACE_SOURCES, but they are shipped servers with console scripts and
    # skills, so the registry has to know them (a package without a record is
    # exactly what the parity test refuses).
    SourceSpec(
        name="marketplace",
        kind="profile",
        package="marketplace-connector",
        module="marketplace_connector.server",
        console_script="marketplace-mcp",
        tool_prefix="",
        tools=39,
    ),
)

BY_NAME: dict[str, SourceSpec] = {spec.name: spec for spec in SOURCES}


def _mounted() -> tuple[SourceSpec, ...]:
    return tuple(spec for spec in SOURCES if spec.mount is not None)


def _comparable() -> tuple[SourceSpec, ...]:
    return tuple(spec for spec in _mounted() if spec.card_tool is not None)


def _mount_rows() -> tuple[tuple[str, str], ...]:
    """(mount name, module path) rows, with the optional mount name narrowed."""
    rows: list[tuple[str, str]] = []
    for spec in SOURCES:
        if spec.mount is not None:
            rows.append((spec.mount, spec.module))
    return tuple(rows)


def _card_tools() -> dict[str, str]:
    """Source -> card tool, keeping the registry's order."""
    tools: dict[str, str] = {}
    for spec in SOURCES:
        if spec.card_tool is not None:
            tools[spec.name] = spec.card_tool
    return tools


def _selfcheck_rows() -> tuple[tuple[str, str, str], ...]:
    """(source, module path, selfcheck function) rows in registry order."""
    rows: list[tuple[str, str, str]] = []
    for spec in SOURCES:
        if spec.selfcheck is not None:
            rows.append((spec.name, spec.module, spec.selfcheck))
    return tuple(rows)


def _config_rows() -> tuple[tuple[str, str, str], ...]:
    """(config key, console script, note) rows in registry order."""
    rows: list[tuple[str, str, str]] = []
    for spec in SOURCES:
        if spec.config_key is not None:
            rows.append((spec.config_key, spec.console_script, spec.note))
    return tuple(rows)


def _unblock_pages() -> dict[str, str]:
    """Browser-backed source -> page a human opens, in registry order."""
    pages: dict[str, str] = {}
    for spec in SOURCES:
        if spec.unblock_page is not None:
            pages[spec.name] = spec.unblock_page
    return pages


# The unified server's mount table. Kept as a function of the registry rather
# than a second literal: MARKETPLACE_SOURCES and this table must name the same
# vocabulary, or a selected source silently mounts nothing.
MOUNTS: tuple[tuple[str, str], ...] = _mount_rows()

# Canonical names MARKETPLACE_SOURCES may select: everything the unified server
# mounts. The project's own profile servers (compare excepted) are not sources.
MOUNTABLE: frozenset[str] = frozenset(spec.name for spec in _mounted())

CAPABILITIES: dict[str, dict[str, Any]] = {
    spec.name: {
        "access": spec.access,
        "requires_cdp": spec.requires_cdp,
        "requires_login": spec.requires_login,
        "currency": spec.currency,
        "text_search": spec.text_search,
    }
    for spec in SOURCES
    if spec.kind == "marketplace"
}
# The static table above says what a source's TIER is. Whether the browser is
# actually involved is a configuration question, so it lives in the effective
# capability fields and not here.


# The route a source takes. Every source but Wildberries has a fixed one;
# WB's is configured, because its API hosts refuse some addresses and the
# connector then reads the same pages through the operator's Chrome.
ROUTE_API = "api"
ROUTE_CDP = "cdp"
ROUTE_DOM = "dom"
ROUTE_API_THEN_DOM = "api_then_dom"


def wb_route(transport: str, dom_fallback: bool) -> str:
    """The route WB takes with this transport and fallback setting.

    One implementation for the connector and for the inventory, so the two
    cannot disagree: ``dom`` reads WB through the scraping Chrome, ``api``
    uses the public JSON hosts only, and ``api_then_dom`` tries the API first
    and falls back to the browser when WB refuses this address.
    """
    if (transport or "").strip().lower() == ROUTE_DOM:
        return ROUTE_DOM
    return ROUTE_API_THEN_DOM if dom_fallback else ROUTE_API


def source_route(name: str, *, env: Mapping[str, str] | None = None) -> str:
    """The route a source takes now, following its configuration.

    The environment is read here rather than in the connector so that a
    caller can ask the question without importing anything (the operator CLI
    must not mount the connectors). A caller that HAS the source's own
    settings — the WB connector, which may read them from a .env file —
    passes them through ``wb_route`` instead.
    """
    environ = os.environ if env is None else env
    if name == "wildberries":
        transport = (environ.get("WB_TRANSPORT") or ROUTE_API).strip().lower()
        fallback = (environ.get("WB_DOM_FALLBACK") or "1").strip().lower() not in {"0", "false", "no", "off"}
        return wb_route(transport, fallback)
    spec = BY_NAME.get(name)
    if spec is None:
        return "unknown"
    if spec.access == "cdp":
        return ROUTE_CDP
    if spec.access == "http_or_cdp":
        return "http_then_cdp"
    if spec.access == "graphql_or_cdp":
        return "graphql_then_cdp"
    return ROUTE_API


ALIASES: dict[str, str] = {alias: spec.name for spec in SOURCES for alias in spec.aliases}

SELFCHECKS: tuple[tuple[str, str, str], ...] = _selfcheck_rows()

CONFIG_SERVERS: tuple[tuple[str, str, str], ...] = _config_rows()

UNBLOCK_PAGES: dict[str, str] = _unblock_pages()

LOGIN_UNBLOCK_SOURCES: frozenset[str] = frozenset(spec.name for spec in SOURCES if spec.login_wall)

DRIFT_PROBE_SOURCES: frozenset[str] = frozenset(spec.name for spec in SOURCES if spec.drift_probe)

# A source takes part in comparison exactly when it exposes a card tool compare
# can call: the search adapters and the card adapters have to come in pairs, or
# compare advertises a source whose offers it cannot verify.
COMPARE_SOURCES: tuple[str, ...] = tuple(spec.name for spec in _comparable())

CARD_TOOL_NAMES: dict[str, str] = _card_tools()

FOREIGN_CURRENCY_SOURCES: tuple[str, ...] = tuple(spec.name for spec in _comparable() if spec.currency != "rub")

MOUNT_GATED_SOURCES: frozenset[str] = frozenset(spec.name for spec in _mounted() if spec.mount_gated)
