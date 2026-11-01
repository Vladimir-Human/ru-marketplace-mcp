"""Tests for the shared source registry.

The registry exists because the fact "this project ships source X" was copied
into every table that needed it, and the copies had already drifted apart. So
these tests check the two things that keeps true: each record is complete and
unambiguous on its own, and every derived table is a faithful projection of the
records rather than a second list that happens to look similar.

``test_source_registry_parity.py`` in marketplace-connector checks the sites
that cannot import Python (the workspace manifests, ``server.json``, the CI
inventory, the literals existing tests parse out of source).
"""

from __future__ import annotations

from mcp_core import sources

_MARKETPLACE_KEYS = {"access", "requires_cdp", "requires_login", "currency", "text_search"}


def test_every_record_names_a_package_module_and_console_script():
    """A record with a blank fact would silently produce a blank table row."""
    for spec in sources.SOURCES:
        assert spec.name == spec.name.strip().lower().replace("-", "_"), spec.name
        assert spec.package.endswith("-connector"), spec
        assert spec.module.startswith(spec.package.replace("-", "_")), spec
        assert spec.module.endswith(".server"), spec
        assert spec.console_script.endswith("-mcp"), spec
        assert spec.tool_prefix == "" or spec.tool_prefix.endswith("_"), spec
        assert spec.tools > 0, spec
        assert spec.kind in {"marketplace", "profile"}, spec

    assert sources.MOUNTS, "the registry must mount something"
    assert sources.SOURCES, "the registry must not be empty"


def test_names_aliases_and_lookups_agree():
    names = [spec.name for spec in sources.SOURCES]
    assert len(names) == len(set(names))
    assert set(sources.BY_NAME) == set(names)

    for spec in sources.SOURCES:
        assert sources.BY_NAME[spec.name] is spec
        for alias in spec.aliases:
            assert sources.ALIASES[alias] == spec.name, alias

    # An alias must not shadow another source's canonical name.
    assert not set(sources.ALIASES) & set(names)


def test_the_mount_table_is_the_mounted_records_in_order():
    rows = [(spec.mount, spec.module) for spec in sources.SOURCES if spec.mount is not None]
    assert tuple(rows) == sources.MOUNTS

    mounted = {spec.name for spec in sources.SOURCES if spec.mount is not None}
    assert mounted == sources.MOUNTABLE
    assert "marketplace" not in sources.MOUNTABLE, "the unified server is not a source to select"
    assert "compare" in sources.MOUNTABLE


def test_capabilities_cover_exactly_the_marketplace_records():
    marketplaces = {spec.name for spec in sources.SOURCES if spec.kind == "marketplace"}
    profiles = {spec.name for spec in sources.SOURCES if spec.kind == "profile"}

    assert set(sources.CAPABILITIES) == marketplaces
    assert not marketplaces & profiles
    for name, capability in sources.CAPABILITIES.items():
        assert set(capability) == _MARKETPLACE_KEYS, name
        assert capability["currency"] in {"rub", "cny"}, name
        assert bool(capability["requires_login"]) <= bool(capability["requires_cdp"]), name


def test_every_operator_table_row_points_at_a_record():
    """The CLI's tables are projections, so every row must resolve back."""
    for name, module, selfcheck in sources.SELFCHECKS:
        spec = sources.BY_NAME[name]
        assert (module, selfcheck) == (spec.module, spec.selfcheck)

    for key, script, note in sources.CONFIG_SERVERS:
        matched = [spec for spec in sources.SOURCES if spec.config_key == key]
        assert len(matched) == 1, key
        assert (script, note) == (matched[0].console_script, matched[0].note)

    assert sources.UNBLOCK_PAGES
    for name, page in sources.UNBLOCK_PAGES.items():
        assert sources.BY_NAME[name].unblock_page == page
        assert page.startswith("https://"), page

    assert set(sources.UNBLOCK_PAGES) >= sources.LOGIN_UNBLOCK_SOURCES
    assert set(sources.BY_NAME) >= sources.DRIFT_PROBE_SOURCES


def test_comparison_tables_derive_from_the_card_tools():
    """A source is comparable exactly when it exposes a card tool to verify."""
    with_cards = {spec.name: spec.card_tool for spec in sources.SOURCES if spec.card_tool is not None}

    assert with_cards == sources.CARD_TOOL_NAMES
    assert tuple(with_cards) == sources.COMPARE_SOURCES
    assert set(sources.CARD_TOOL_NAMES) == set(sources.COMPARE_SOURCES)
    for name, tool in with_cards.items():
        assert tool == sources.BY_NAME[name].tool_prefix + "card", name

    foreign = tuple(name for name in sources.COMPARE_SOURCES if sources.BY_NAME[name].currency != "rub")
    assert foreign == sources.FOREIGN_CURRENCY_SOURCES
    assert "taobao" in sources.FOREIGN_CURRENCY_SOURCES


def test_mount_gated_sources_are_mounted_but_need_configuration():
    gated = {spec.name for spec in sources.SOURCES if spec.mount_gated}
    assert gated == sources.MOUNT_GATED_SOURCES
    assert gated <= sources.MOUNTABLE
    for name in gated:
        assert sources.BY_NAME[name].mount is not None
