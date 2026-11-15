#!/usr/bin/env python3
"""Fold bilingual PR fragments into a release, or check a PR's fragment requirement.

Fragments are the exact release Markdown: known Keep a Changelog section headings,
then Russian and English bullets. Language and bullet parity are not machine-checked.
All inputs are validated before a release write; dry-run and PR mode are read-only.

START_MODULE_CONTRACT
PURPOSE: Build release sections from changes/<PR>.md and enforce fragments for package PRs.
SCOPE: Markdown validation, numeric PR ordering, Unreleased migration, LF writes, fragment deletion, pure PR decision and CLI.
DEPENDS: stdlib argparse,datetime,json,re,sys,collections.abc,pathlib.
LINKS: M-CHANGELOG-FRAGMENTS, R-CHANGELOG-FRAGMENTS.
START_MODULE_MAP
  _markdown_lines  identify real headings/bullets without interpreting fenced code examples
  parse_fragment   validate headings and bullets with filename/line diagnostics
  check_pr         pure decision from PR metadata, paths and optional fragment text
  build_release    plan a section and updated changelog without touching files
  main             read CLI inputs, print a dry-run or apply the validated release
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
PR_NAME = re.compile(r"[1-9][0-9]*\.md")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
BULLET = re.compile(r"^ {0,3}[-+*][ \t]+\S")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*?)[ \t]*$")
FENCE = re.compile(r"^( {0,3}(?:(?:[-+*]|[0-9]+[.)])[ \t]+)?)(`{3,}|~{3,})(.*)$")


# START_BLOCK_MARKDOWN_SCANNING


def _markdown_lines(text: str) -> Iterator[tuple[int, int, str, bool]]:
    """Yield line number, offset, raw line and whether it is outside fenced code."""
    fence = ""
    length = 0
    indent = 0
    offset = 0
    for lineno, line in enumerate(text.splitlines(keepends=True), 1):
        raw = line.rstrip("\r\n")
        outside = not fence
        if fence:
            if re.fullmatch(rf" {{0,{indent + 3}}}{re.escape(fence)}{{{length},}}[ \t]*", raw):
                fence = ""
        else:
            match = FENCE.fullmatch(raw)
            if match and (match[2][0] != "`" or "`" not in match[3]):
                fence, length, indent = match[2][0], len(match[2]), len(match[1])
                # A list-marker opener is itself a bullet, but its code is not.
                outside = bool(match[1].strip())
        yield lineno, offset, line, outside
        offset += len(line)


def _headings(text: str) -> list[tuple[int, int, int, str]]:
    """Only real ATX headings, as (start, end, level, title), not code examples."""
    headings = []
    for _, offset, line, outside in _markdown_lines(text):
        match = HEADING.fullmatch(line.rstrip("\r\n")) if outside else None
        if match:
            headings.append((offset, offset + len(line), len(match[1]), match[2]))
    return headings


# END_BLOCK_MARKDOWN_SCANNING

# START_BLOCK_FRAGMENT_VALIDATION


def parse_fragment(text: str, filename: str) -> dict[str, str]:
    """Validate each section independently; retain bullets and their continuations."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    heading_line = 1
    body: list[str] = []
    has_bullet = False

    def finish() -> None:
        if current is None:
            return
        content = "".join(body).strip("\n")
        if not has_bullet:
            raise ValueError(f"{filename}:{heading_line}: section {current} needs at least one top-level bullet")
        sections.setdefault(current, []).append(content)

    for lineno, _, line, outside in _markdown_lines(text):
        match = HEADING.fullmatch(line.rstrip("\r\n")) if outside else None
        if match:
            if len(match[1]) != 3 or match[2] not in SECTIONS:
                raise ValueError(
                    f"{filename}:{lineno}: use only ### headings from {', '.join(SECTIONS)}; no ## headings"
                )
            finish()
            current = match[2]
            heading_line = lineno
            body = []
            has_bullet = False
        elif current is None:
            if line.strip():
                raise ValueError(f"{filename}:{lineno}: entry must start with a ### section heading")
        else:
            body.append(line)
            has_bullet = has_bullet or (outside and BULLET.match(line) is not None)
    finish()
    if not sections:
        raise ValueError(f"{filename}:1: fragment needs a section with at least one bullet")
    return {section: "\n\n".join(chunks) for section, chunks in sections.items()}


# END_BLOCK_FRAGMENT_VALIDATION

# START_BLOCK_PR_DECISION


def check_pr(
    pr_number: int,
    changed_paths: Sequence[str],
    labels: Sequence[str],
    author: str,
    fragment_text: str | None = None,
) -> str | None:
    """Return None for a pass, otherwise a single-line actionable failure; no I/O."""
    if not any(path.startswith("packages/") for path in changed_paths):
        return None
    if author == "dependabot[bot]" or "no-changelog" in labels:
        return None
    filename = f"changes/{pr_number}.md"
    hint = f"Add {filename} with RU then EN entries, or apply the no-changelog label."
    if filename not in changed_paths or fragment_text is None:
        return hint
    try:
        parse_fragment(fragment_text, filename)
    except ValueError as exc:
        return f"{exc} {hint}"
    return None


# END_BLOCK_PR_DECISION

# START_BLOCK_RELEASE_PLANNING


def _hand_sections(text: str) -> tuple[str, dict[str, str]]:
    """Group known manual headings without validating or discarding any hand text."""
    heads = [head for head in _headings(text) if head[2] == 3 and head[3] in SECTIONS]
    preamble = text[: heads[0][0]] if heads else text
    grouped: dict[str, list[str]] = {}
    for index, head in enumerate(heads):
        end = heads[index + 1][0] if index + 1 < len(heads) else len(text)
        grouped.setdefault(head[3], []).append(text[head[1] : end].strip("\n"))
    return preamble.strip("\n"), {section: "\n\n".join(chunks) for section, chunks in grouped.items()}


def build_release(changelog: str, fragments: Sequence[tuple[str, str]], version: str, date: str) -> tuple[str, str]:
    """Plan the printed section and complete file; fragments must be in numeric PR order.

    START_CONTRACT: build_release
    PURPOSE: Keep all Unreleased text and ordered fragment entries in one new release.
    INPUTS: Changelog text, ordered filename/text pairs, X.Y.Z and YYYY-MM-DD.
    OUTPUTS: (release section ending in LF, complete updated changelog).
    SIDE_EFFECTS: None; invalid input raises ValueError before any write.
    END_CONTRACT
    """
    if not VERSION.fullmatch(version):
        raise ValueError(f"version must be X.Y.Z, got {version!r}")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    dt.date.fromisoformat(date)
    headings = [head for head in _headings(changelog) if head[2] == 2]
    if any(re.fullmatch(rf"\[{re.escape(version)}\](?:[ \t].*)?", head[3]) for head in headings):
        raise ValueError(f"CHANGELOG.md already contains version [{version}]")
    unreleased = next((head for head in headings if head[3] == "[Unreleased]"), None)
    if unreleased is None:
        raise ValueError("CHANGELOG.md needs a ## [Unreleased] heading")
    end = next((head[0] for head in headings if head[0] >= unreleased[1]), len(changelog))
    preamble, hand = _hand_sections(changelog[unreleased[1] : end])
    grouped: dict[str, list[str]] = {section: [content] for section, content in hand.items()}
    for filename, text in fragments:
        for section, content in parse_fragment(text, filename).items():
            grouped.setdefault(section, []).append(content)

    parts = [preamble] if preamble else []
    for section in SECTIONS:
        if section in grouped:
            content = "\n\n".join(grouped[section])
            parts.append(f"### {section}\n{content}")
    section_text = f"## [{version}] - {date}\n"
    if parts:
        section_text += "\n" + "\n\n".join(parts).rstrip("\n") + "\n"
    prefix = changelog[: unreleased[1]].rstrip("\n") + "\n\n"
    updated = prefix + section_text + "\n" + changelog[end:]
    return section_text, updated


def _fragments(root: Path) -> list[Path]:
    paths = [path for path in (root / "changes").glob("*.md") if path.name != "README.md"]
    for path in paths:
        if not PR_NAME.fullmatch(path.name):
            raise ValueError(f"changes/{path.name}:1: fragment filename must be a positive PR number, e.g. 12.md")
    return sorted(paths, key=lambda path: (int(path.stem), path.name))


# END_BLOCK_RELEASE_PLANNING

# START_BLOCK_CLI


def _read_fragment(path: Path, filename: str) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        lineno = raw[: exc.start].count(b"\n") + 1
        raise ValueError(f"{filename}:{lineno}: fragment must be UTF-8") from exc


def _check_pr_inputs(args: argparse.Namespace, root: Path) -> int:
    if args.pr_number < 1:
        raise ValueError("PR number must be positive")
    labels = json.loads(args.labels)
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValueError("--labels must be a JSON list of label names")
    # --name-only --no-renames -z includes removed paths and both sides of moves.
    paths = Path(args.changed_files).read_bytes().decode("utf-8").split("\0")
    problem = check_pr(args.pr_number, paths, labels, args.author)
    filename = f"changes/{args.pr_number}.md"
    if problem is not None and filename in paths:
        try:
            text = _read_fragment(root / filename, filename)
        except OSError:
            pass  # a deleted or unreadable fragment keeps the missing-fragment hint
        except ValueError as exc:
            problem = f"{exc} {problem}"
        else:
            problem = check_pr(args.pr_number, paths, labels, args.author, text)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--version", help="release version X.Y.Z")
    mode.add_argument("--check-pr", action="store_true", help="check the PR fragment requirement")
    parser.add_argument("--date", help="release date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="print the release section and write nothing")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--changed-files", help="file containing NUL-separated git diff paths")
    parser.add_argument("--labels", help="JSON list of PR label names")
    parser.add_argument("--author", help="PR author login")
    args = parser.parse_args(argv)
    if args.check_pr:
        if any(value is None for value in (args.pr_number, args.changed_files, args.labels, args.author)):
            parser.error("--check-pr needs --pr-number, --changed-files, --labels and --author")
        if args.date or args.dry_run:
            parser.error("--date and --dry-run are release-only options")
    elif any(value is not None for value in (args.pr_number, args.changed_files, args.labels, args.author)):
        parser.error("PR inputs require --check-pr")
    try:
        if args.check_pr:
            return _check_pr_inputs(args, root)
        paths = _fragments(root)
        fragments = [(f"changes/{path.name}", _read_fragment(path, f"changes/{path.name}")) for path in paths]
        changelog = root / "CHANGELOG.md"
        section, updated = build_release(
            changelog.read_text(encoding="utf-8"), fragments, args.version, args.date or dt.date.today().isoformat()
        )
        if args.dry_run:
            # The section is Russian first; a Windows console code page (cp1252) cannot
            # encode it, so print UTF-8 whatever the console default is.
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(section, end="")
            return 0
        with changelog.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(updated)
        for path in paths:
            path.unlink()
        print(f"Built [{args.version}] from {len(paths)} fragments in CHANGELOG.md")
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


# END_BLOCK_CLI

if __name__ == "__main__":
    raise SystemExit(main())
