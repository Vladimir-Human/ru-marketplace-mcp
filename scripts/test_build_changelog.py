"""R-CHANGELOG-FRAGMENTS: temporary-tree folding, in-memory PR/Markdown and CI checks.

The Markdown below is deliberately hand-written release input, not captured marketplace
content. No transport, clock, filesystem or subprocess is faked or monkeypatched.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_changelog as builder

OLD_RELEASE = "## [2.9.3] - 2026-11-15\n\n### Fixed\n- Старое.\n- Old.\n"
HEADER = "# Changelog\n\nРусский первый, английский ниже.\n\n## [Unreleased]\n\n"
ENTRY = "### Fixed\n- Исправлено (#12).\n- Fixed (#12).\n"
RELEASE_ARGS = ["--version", "3.0.0", "--date", "2026-11-16"]


def _tree(tmp_path: Path, hand: str = "") -> Path:
    (tmp_path / "CHANGELOG.md").write_text(HEADER + hand + OLD_RELEASE, encoding="utf-8", newline="\n")
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / ".gitkeep").write_bytes(b"")
    return tmp_path


def _fragment(root: Path, name: str = "12.md", text: str = ENTRY) -> Path:
    path = root / "changes" / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


# START_BLOCK_RELEASE_TESTS


def test_two_fragments_fold_in_section_and_numeric_pr_order(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _fragment(
        root, "100.md", "### Fixed\n- Сто (#100).\n- Hundred (#100).\n\n### Added\n- Новое (#100).\n- New (#100).\n"
    )
    _fragment(
        root,
        "12.md",
        "### Fixed\n- Двенадцать (#12).\n- Twelve (#12).\n\n### Security\n- Защита (#12).\n- Security (#12).\n",
    )

    assert builder.main(RELEASE_ARGS, root=root) == 0
    expected = (
        "## [3.0.0] - 2026-11-16\n\n"
        "### Added\n- Новое (#100).\n- New (#100).\n\n"
        "### Fixed\n- Двенадцать (#12).\n- Twelve (#12).\n\n- Сто (#100).\n- Hundred (#100).\n\n"
        "### Security\n- Защита (#12).\n- Security (#12).\n\n"
    )
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == HEADER + expected + OLD_RELEASE
    assert list((root / "changes").iterdir()) == [root / "changes" / ".gitkeep"]
    assert b"\r" not in (root / "CHANGELOG.md").read_bytes()


def test_hand_text_is_preserved_before_fragment_entries(tmp_path: Path) -> None:
    hand = "Заметка вручную.\nManual note.\n\n### Fixed\n- Ручное.\n- Manual.\n\n"
    root = _tree(tmp_path, hand)
    _fragment(root)

    assert builder.main(RELEASE_ARGS, root=root) == 0
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith(HEADER + "## [3.0.0] - 2026-11-16\n\n" + hand + "- Исправлено (#12).\n- Fixed (#12).\n\n")
    assert text.count("### Fixed") == 2  # one new section, one untouched old release
    assert text.endswith(OLD_RELEASE)


def test_all_keep_a_changelog_sections_use_the_standard_order(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    sections = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
    _fragment(
        root, text="\n".join(f"### {section}\n- RU {section}.\n- EN {section}.\n" for section in reversed(sections))
    )

    assert builder.main(RELEASE_ARGS, root=root) == 0
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8").split(OLD_RELEASE)[0]
    positions = [text.index(f"### {section}") for section in sections]
    assert positions == sorted(positions)


def test_dry_run_prints_the_same_section_and_touches_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, "Ручной текст.\nHand text.\n\n")
    _fragment(root)
    before = _snapshot(root)

    assert builder.main([*RELEASE_ARGS, "--dry-run"], root=root) == 0
    section = capsys.readouterr().out
    assert section.startswith("## [3.0.0] - 2026-11-16\n\nРучной текст.\nHand text.\n\n### Fixed\n")
    assert ENTRY in section
    assert _snapshot(root) == before
    assert builder.main(RELEASE_ARGS, root=root) == 0
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == HEADER + section + "\n" + OLD_RELEASE


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("### Unknown\n- Entry.\n", 1),
        ("### Added\nOnly prose.\n", 1),
        ("### Added\n- Entry.\n## [3.0.0]\n", 3),
        ("### Added\n- Entry.\n\n### Fixed\n", 4),
        ("", 1),
        ("- No section.\n", 1),
    ],
)
def test_bad_fragment_names_the_file_and_line_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, line: int
) -> None:
    root = _tree(tmp_path)
    _fragment(root, "1.md")  # a valid earlier fragment must not be consumed on failure
    _fragment(root, "12.md", text)
    before = _snapshot(root)

    assert builder.main(RELEASE_ARGS, root=root) == 1
    assert f"changes/12.md:{line}:" in capsys.readouterr().err
    assert _snapshot(root) == before


def test_odd_bullet_counts_and_continuations_are_kept(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    fragment = "### Changed\n- Нечётная заметка (#12).\n  continuation with `code`.\n  - nested detail\n"
    _fragment(root, text=fragment)

    assert builder.main(RELEASE_ARGS, root=root) == 0
    assert fragment in (root / "CHANGELOG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("dry_run", [False, True])
def test_an_existing_version_is_refused_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], dry_run: bool
) -> None:
    root = _tree(tmp_path)
    _fragment(root)
    before = _snapshot(root)

    args = ["--version", "2.9.3", *(["--dry-run"] if dry_run else [])]
    assert builder.main(args, root=root) == 1
    assert "2.9.3" in capsys.readouterr().err
    assert _snapshot(root) == before


def test_no_fragments_builds_an_empty_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _tree(tmp_path)
    assert builder.main([*RELEASE_ARGS, "--dry-run"], root=root) == 0
    assert capsys.readouterr().out == "## [3.0.0] - 2026-11-16\n"


@pytest.mark.parametrize("name", ["oops.md", "0.md"])
def test_non_pr_fragment_names_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    root = _tree(tmp_path)
    _fragment(root, name)
    before = _snapshot(root)
    assert builder.main(RELEASE_ARGS, root=root) == 1
    assert name in capsys.readouterr().err
    assert _snapshot(root) == before


@pytest.mark.parametrize("args", [["--version", "1.2"], ["--version", "1.2.3", "--date", "2026-02-30"]])
def test_bad_release_arguments_are_refused(tmp_path: Path, args: list[str]) -> None:
    root = _tree(tmp_path)
    before = _snapshot(root)
    assert builder.main(args, root=root) == 1
    assert _snapshot(root) == before


def test_missing_unreleased_heading_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _tree(tmp_path)
    (root / "CHANGELOG.md").write_text("# Changelog\n\n" + OLD_RELEASE, encoding="utf-8", newline="\n")
    before = _snapshot(root)
    assert builder.main(RELEASE_ARGS, root=root) == 1
    assert "Unreleased" in capsys.readouterr().err
    assert _snapshot(root) == before


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_manual_headings_stay_inside_their_example(fence: str) -> None:
    """R-CHANGELOG-FRAGMENTS.fold: Markdown examples must not split or reorder hand text."""
    hand = f"### Fixed\n- Ручное.\n- Manual.\n\n{fence}markdown\n## Migration details\n### Added\n- Example only.\n{fence}\n\n"
    fragment = "### Added\n- Новое.\n- New.\n"
    section, updated = builder.build_release(
        HEADER + hand + OLD_RELEASE, [("changes/12.md", fragment)], "3.0.0", "2026-11-16"
    )
    expected = "## [3.0.0] - 2026-11-16\n\n" + fragment + "\n" + hand.rstrip("\n") + "\n"
    assert section == expected
    assert updated == HEADER + expected + "\n" + OLD_RELEASE


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_fragment_examples_are_not_headings_or_top_level_bullets(fence: str) -> None:
    """R-CHANGELOG-FRAGMENTS.validation: literal code is neither a section nor a bullet."""
    example = f"{fence}markdown\n# comment\n## Example\n### Added\n- Example only.\n{fence}\n"
    text = "### Changed\n- Изменено.\n- Changed.\n\n" + example
    assert builder.parse_fragment(text, "changes/12.md") == {"Changed": text.split("\n", 1)[1].rstrip("\n")}
    with pytest.raises(ValueError, match=r"changes/12\.md:1:.*bullet"):
        builder.parse_fragment(f"### Changed\n{fence}\n- Example only.\n{fence}\n", "changes/12.md")


@pytest.mark.parametrize("mode", ["release", "pr"])
def test_invalid_utf8_fragment_reports_its_name_and_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """R-CHANGELOG-FRAGMENTS.validation: decoding errors retain actionable diagnostics."""
    root = _tree(tmp_path)
    (root / "changes" / "12.md").write_bytes(b"### Fixed\n- \xff\n")
    changed = root / "changed-paths"
    changed.write_bytes(b"packages/demo/file\0changes/12.md\0")
    before = _snapshot(root)
    args = (
        RELEASE_ARGS
        if mode == "release"
        else [
            "--check-pr",
            "--pr-number",
            "12",
            "--changed-files",
            str(changed),
            "--labels",
            "[]",
            "--author",
            "contributor",
        ]
    )
    assert builder.main(args, root=root) == 1
    error = capsys.readouterr().err
    assert "changes/12.md:2:" in error
    if mode == "pr":
        assert "no-changelog" in error and len(error.splitlines()) == 1
    assert _snapshot(root) == before


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
@pytest.mark.parametrize("marker", ["-", "+", "*"])
def test_top_level_bullet_indentation_uses_markdown_rules(indent: str, marker: str) -> None:
    """R-CHANGELOG-FRAGMENTS.validation: up to three spaces is still a top-level list."""
    body = f"{indent}{marker} RU.\n{indent}{marker} EN."
    assert builder.parse_fragment("### Fixed\n" + body + "\n", "changes/12.md") == {"Fixed": body}
    with pytest.raises(ValueError, match=r"changes/12\.md:1:.*bullet"):
        builder.parse_fragment(f"### Fixed\n    {marker} Only indented code.\n", "changes/12.md")


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fences_in_list_items_keep_their_literal_headings(fence: str) -> None:
    """R-CHANGELOG-FRAGMENTS.fold/validation: a list-item example is not a new section."""
    body = f"- RU.\n- EN.\n- {fence}markdown\n  ### Added\n  - example\n  {fence}"
    assert builder.parse_fragment("### Changed\n" + body + "\n", "changes/12.md") == {"Changed": body}
    section, _ = builder.build_release(
        HEADER + "### Changed\n" + body + "\n\n" + OLD_RELEASE, [], "3.0.0", "2026-11-16"
    )
    assert section == "## [3.0.0] - 2026-11-16\n\n### Changed\n" + body + "\n"


# END_BLOCK_RELEASE_TESTS

# START_BLOCK_PR_TESTS


@pytest.mark.parametrize(
    ("paths", "labels", "author", "fragment", "passes"),
    [
        (["docs/guide.md", "packages-other/file"], [], "contributor", None, True),
        (["packages/demo/file"], [], "dependabot[bot]", None, True),
        (["packages/demo/file"], ["no-changelog"], "contributor", None, True),
        (["packages/demo/file", "changes/12.md"], [], "contributor", ENTRY, True),
        (["packages/demo/file"], [], "contributor", None, False),
        (["packages/demo/file", "changes/13.md"], [], "contributor", ENTRY, False),
        (["packages/demo/file", "changes/12.md"], [], "contributor", "### Wrong\n- Entry.\n", False),
        (["packages/demo/file", "changes/12.md"], [], "contributor", None, False),
        (["packages/demo/file"], ["No-changelog"], "dependabot", None, False),
    ],
)
def test_pure_pr_decision_branches(
    paths: list[str], labels: list[str], author: str, fragment: str | None, passes: bool
) -> None:
    problem = builder.check_pr(12, paths, labels, author, fragment)
    assert (problem is None) == passes
    if not passes:
        assert problem is not None
        assert "changes/12.md" in problem and "no-changelog" in problem
        assert "\n" not in problem


@pytest.mark.parametrize(
    ("paths", "labels", "author", "fragment", "expected"),
    [
        (["docs/guide.md"], "[]", "contributor", None, 0),
        (["packages/demo/file"], "[]", "dependabot[bot]", None, 0),
        (["packages/demo/file"], '["no-changelog"]', "contributor", None, 0),
        (["packages/demo/file", "changes/12.md"], "[]", "contributor", ENTRY, 0),
        (["packages/demo/file"], "[]", "contributor", None, 1),
        (["packages/demo/file", "changes/13.md"], "[]", "contributor", ENTRY, 1),
        (["packages/demo/file", "changes/12.md"], "[]", "contributor", "### Fixed\nNo bullet.\n", 1),
    ],
)
def test_check_pr_cli_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    paths: list[str],
    labels: str,
    author: str,
    fragment: str | None,
    expected: int,
) -> None:
    root = _tree(tmp_path)
    if fragment is not None:
        _fragment(root, "13.md" if "changes/13.md" in paths else "12.md", fragment)
    changed = root / "changed-paths"
    # git diff -z keeps spaces, Unicode and even newlines inside a path intact.
    changed.write_bytes(("\0".join([*paths, "docs/имя с\nпереносом.md"]) + "\0").encode("utf-8"))
    before = _snapshot(root)

    code = builder.main(
        ["--check-pr", "--pr-number", "12", "--changed-files", str(changed), "--labels", labels, "--author", author],
        root=root,
    )
    assert code == expected
    output = capsys.readouterr()
    if expected:
        assert "changes/12.md" in output.err and "no-changelog" in output.err
        assert len(output.err.splitlines()) == 1
    assert _snapshot(root) == before


def test_real_cli_uses_its_copied_tree(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _fragment(root)
    scripts = root / "scripts"
    scripts.mkdir()
    script = scripts / "build_changelog.py"
    shutil.copyfile(builder.__file__, script)
    before = _snapshot(root)

    result = subprocess.run(
        [sys.executable, str(script), *RELEASE_ARGS, "--dry-run"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ENTRY in result.stdout
    assert _snapshot(root) == before


# END_BLOCK_PR_TESTS

# START_BLOCK_WORKFLOW_TESTS


def _assert_ci_fragment_step(text: str) -> None:
    trigger = re.search(r"^  pull_request:\n(.*?)(?=^  \S)", text, re.M | re.S)
    assert trigger is not None
    assert "types: [opened, synchronize, reopened, labeled, unlabeled]" in trigger.group(1)
    lint = re.search(r"^  lint:\n(.*?)(?=^  \S)", text, re.M | re.S)
    assert lint is not None
    body = lint.group(1)
    assert "fetch-depth: 0" in body
    step = body[body.rindex("      - name:") :]
    assert step.startswith("      - name: CHANGELOG fragment\n")
    assert "if: github.event_name == 'pull_request'" in step
    assert 'git diff --name-only --no-renames -z "$PR_BASE_SHA...$PR_HEAD_SHA"' in step
    assert "--check-pr" in step and "build_changelog.py" in step
    for source in ("number", "base.sha", "head.sha", "labels.*.name", "user.login"):
        assert f"github.event.pull_request.{source}" in step
    for option in ("--pr-number", "--changed-files", "--labels", "--author"):
        assert option in step


def test_ci_fragment_step_and_label_events() -> None:
    """The workflow gathers git/PR inputs only and reruns the final lint step for labels."""
    workflow = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    _assert_ci_fragment_step(workflow.read_text(encoding="utf-8"))


# END_BLOCK_WORKFLOW_TESTS
