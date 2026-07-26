#!/usr/bin/env python3
"""Fail if a connector server writes to stdout.

An MCP stdio server owns stdout: every byte there must be part of the JSON-RPC
stream. A stray ``print()`` — or any explicit ``sys.stdout`` write — corrupts the
protocol, and the failure surfaces as a baffling client-side parse error rather
than anything resembling its cause. That makes it worth a dedicated check.

Diagnostics belong on stderr (``mcp_core.logging.log_event``) or in the FastMCP
``Context`` logging methods.

Usage:
    python scripts/check_no_print.py [paths...]

With no arguments, every ``packages/*/src`` tree is scanned. Runs as a
pre-commit hook and is safe to run by hand.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class StdoutWriteVisitor(ast.NodeVisitor):
    """Collects ``print(...)`` and ``sys.stdout.*`` writes with line numbers."""

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # Bare print(...) — unless explicitly redirected to stderr, which is fine.
        if isinstance(func, ast.Name) and func.id == "print":
            redirected_to_stderr = any(kw.arg == "file" and _is_sys_stderr(kw.value) for kw in node.keywords)
            if not redirected_to_stderr:
                self.violations.append((node.lineno, "print() writes to stdout"))

        # sys.stdout.write(...) / sys.stdout.writelines(...)
        if isinstance(func, ast.Attribute) and func.attr in {"write", "writelines"} and _is_sys_stdout(func.value):
            self.violations.append((node.lineno, f"sys.stdout.{func.attr}() corrupts JSON-RPC"))

        self.generic_visit(node)


def _is_sys_stdout(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "stdout" and _is_sys(node.value)


def _is_sys_stderr(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "stderr" and _is_sys(node.value)


def _is_sys(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "sys"


def check_file(path: Path) -> list[str]:
    """Return human-readable violations for one file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: could not parse ({exc.msg})"]

    visitor = StdoutWriteVisitor()
    visitor.visit(tree)

    try:
        display = path.relative_to(REPO_ROOT)
    except ValueError:
        display = path
    return [f"{display}:{lineno}: {reason}" for lineno, reason in visitor.violations]


def collect_default_paths() -> list[Path]:
    return sorted(REPO_ROOT.glob("packages/*/src/**/*.py"))


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] if argv else collect_default_paths()
    # Entry points legitimately run as scripts; only server modules are protocol-critical.
    targets = [p for p in paths if p.suffix == ".py" and p.is_file()]

    violations: list[str] = []
    for path in targets:
        violations.extend(check_file(path))

    if violations:
        sys.stderr.write("stdout writes found in MCP server code:\n")
        for violation in violations:
            sys.stderr.write(f"  {violation}\n")
        sys.stderr.write(
            "\nstdout belongs to the JSON-RPC stream. Use log_event() (stderr) or ctx.info()/ctx.debug() instead.\n"
        )
        return 1

    sys.stderr.write(f"checked {len(targets)} file(s): no stdout writes\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
