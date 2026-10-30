"""The generated tool reference must describe every tool once and stay fresh.

These tests derive their expectations from the public ``list_tools`` surface and
the documented measurement rule, not from a checked-in copy of the table.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import gen_tools_doc as gen

STALE_README = """# Test README

<!-- tools-cost:start -->
old Russian numbers
<!-- tools-cost:end -->

<!-- tools-cost:start -->
old English numbers
<!-- tools-cost:end -->
"""


STALE_DSH_README = """# dsh
<!-- tools-cost-dsh:start -->
| old | table |
<!-- tools-cost-dsh:end -->
"""


def _seed_dsh_readme(root: Path) -> Path:
    path = root / "dsh" / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STALE_DSH_README, encoding="utf-8", newline="\n")
    return path


@pytest.fixture(scope="module")
def measurements() -> tuple[gen.Measurement, ...]:
    return gen.collect_profiles()


def test_generated_docs_are_deterministic_between_runs() -> None:
    first = gen.render_docs(gen.collect_profiles())
    second = gen.render_docs(gen.collect_profiles())

    assert first == second
    assert "\r" not in first


def test_every_tool_of_each_profile_appears_exactly_once(measurements: tuple[gen.Measurement, ...]) -> None:
    for measurement in measurements:
        docs = gen.render_docs((measurement,))
        rows = [line for line in docs.splitlines() if line.startswith("| `")]
        names = [line.split("`")[1] for line in rows]

        assert len(rows) == measurement.tool_count
        assert names == sorted(str(tool["name"]) for tool in measurement.tools)
        assert len(set(names)) == len(names)


def test_profile_table_uses_the_measured_payload_and_stated_token_rule(
    measurements: tuple[gen.Measurement, ...],
) -> None:
    docs = gen.render_docs(measurements)
    assert "a Cyrillic character as 1/3 token and any other character as 1/4" in docs

    def by_the_stated_rule(tool: dict) -> int:
        text = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
        cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
        return int(cyrillic / 3.0 + (len(text) - cyrillic) / 4.0)

    for measurement in measurements:
        expected_tokens = sum(by_the_stated_rule(dict(tool)) for tool in measurement.tools)
        row = (
            f"| {measurement.profile.name} | `{measurement.profile.script}` | "
            f"{measurement.tool_count} | {len(measurement.payload)} | "
            f"≈{gen.format_tokens(expected_tokens)} |"
        )
        assert row in docs
        assert measurement.token_estimate == expected_tokens


def test_check_rejects_a_stale_docs_file_and_accepts_a_fresh_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "README.md").write_text(STALE_README, encoding="utf-8", newline="\n")
    _seed_dsh_readme(tmp_path)
    gen.write_outputs(gen.render_outputs(tmp_path))
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)

    assert gen.main(["--check"]) == 0

    docs = tmp_path / "docs" / "TOOLS.md"
    docs.write_text(docs.read_text(encoding="utf-8") + "\nstale line\n", encoding="utf-8", newline="\n")
    assert gen.main(["--check"]) == 1
    assert gen.STALE_DOCS in capsys.readouterr().err


def test_readme_cost_blocks_are_rewritten_and_checked(
    tmp_path: Path, measurements: tuple[gen.Measurement, ...]
) -> None:
    rendered = gen.render_readme(STALE_README, measurements)

    assert rendered.count("<!-- tools-cost:start -->") == 2
    assert "old Russian numbers" not in rendered
    assert "old English numbers" not in rendered
    for measurement in measurements:
        assert f"≈{gen.format_tokens(measurement.token_estimate)}" in rendered
    assert gen.render_readme(rendered, measurements) == rendered

    (tmp_path / "README.md").write_text(rendered, encoding="utf-8", newline="\n")
    _seed_dsh_readme(tmp_path)
    gen.write_outputs(gen.render_outputs(tmp_path))
    assert gen.check_outputs(tmp_path) == []

    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("Так сделано", "stale Russian numbers\nТак сделано", 1),
        encoding="utf-8",
        newline="\n",
    )
    assert gen.check_outputs(tmp_path) == [gen.STALE_README]


def test_parameter_rendering_covers_required_default_enum_and_array() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "page": {"type": "integer", "default": 1},
            "sort": {"type": "string", "enum": ["price", "rating"], "default": "price"},
            "ids": {"type": "array", "items": {"type": "integer"}},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["query", "ids"],
    }

    rendered = gen.render_parameters(schema)

    assert rendered == (
        'query: string, page: integer = 1, sort: string (one of "price", "rating") = "price", '
        "ids: array[integer], note: string = null"
    )
    assert "'price'" not in rendered


def test_dsh_readme_mode_table_is_generated_and_checked(
    tmp_path: Path, measurements: tuple[gen.Measurement, ...]
) -> None:
    """The dsh README repeats the per-mode cost; it comes from the same measurement."""
    rendered = gen.render_dsh_readme(STALE_DSH_README, measurements)

    assert "| old | table |" not in rendered
    for measurement in measurements:
        row = f"~{gen.format_tokens(measurement.token_estimate)} tokens per request | {measurement.tool_count} |"
        assert row in rendered
    assert gen.render_dsh_readme(rendered, measurements) == rendered
    with pytest.raises(ValueError, match="exactly one tools-cost-dsh block"):
        gen.render_dsh_readme("# no block\n", measurements)

    (tmp_path / "README.md").write_text(STALE_README, encoding="utf-8", newline="\n")
    dsh_readme = _seed_dsh_readme(tmp_path)
    gen.write_outputs(gen.render_outputs(tmp_path))
    assert gen.check_outputs(tmp_path) == []
    dsh_readme.write_text(STALE_DSH_README, encoding="utf-8", newline="\n")
    assert gen.check_outputs(tmp_path) == [gen.STALE_DSH_README]


def test_the_printed_token_numbers_follow_the_stated_rule(measurements: tuple[gen.Measurement, ...]) -> None:
    """The published cost must be the rule's result, not the estimator's echo (audit S4-F18).

    ``Measurement.token_estimate`` *is* ``mcp_wire.wire_tokens(...)``, so
    comparing the two proved nothing. The number is therefore recomputed here,
    by hand, from the documented rule ("a Cyrillic character as 1/3 token and
    any other character as 1/4"), and the figures an operator reads - the
    README cost blocks, the dsh bundle table and the per-profile header - are
    checked against it.
    """

    def by_the_stated_rule(tool: dict) -> int:
        text = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
        cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
        return int(cyrillic / 3.0 + (len(text) - cyrillic) / 4.0)

    readme = gen.render_readme(STALE_README, measurements)
    dsh_readme = gen.render_dsh_readme(STALE_DSH_README, measurements)
    docs = gen.render_docs(measurements)

    expected = {
        measurement.profile.name: gen.format_tokens(sum(by_the_stated_rule(dict(tool)) for tool in measurement.tools))
        for measurement in measurements
    }

    # The README blocks publish the three costs: every number in them is a
    # measured one, and none of the three is missing.
    assert set(re.findall(r"≈([0-9.]+k?)", readme)) == set(expected.values())

    for measurement in measurements:
        printed = expected[measurement.profile.name]
        label = gen.DSH_MODE_LABELS[measurement.profile.name]
        assert f"| {label} | ~{printed} tokens per request | {measurement.tool_count} |" in dsh_readme
        assert f"(≈{printed} tokens)" in docs
