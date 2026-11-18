"""No connector may bound a browser attempt with a bare ``asyncio.wait_for``.

START_MODULE_CONTRACT
PURPOSE: Prove R-CDP-QUEUE-CREDIT.invariant through the source registry and each connector's own source.
SCOPE: Every registered source module; static AST reading only; offline.
DEPENDS: pytest; mcp_core.sources; standard-library ast, importlib.
LINKS: M-SOURCES; M-ATTEMPT-DEADLINE; M-CDP-BUDGET; R-CDP-QUEUE-CREDIT.invariant.
END_MODULE_CONTRACT
START_MODULE_MAP
_called_name: Read the plain name of a call target.
_functions: Index the module's own functions by name.
_reaches_open_page: Tell whether a call, or a local function it calls, enters open_page.
_bare_wait_for_calls: Find every asyncio.wait_for in the module whose awaited work enters open_page.
test_no_connector_bounds_a_browser_attempt_with_a_bare_wait_for: Walk the registry and fail on the first offender.
END_MODULE_MAP
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from mcp_core.sources import SOURCES


def _called_name(func: ast.expr) -> str:
    """The plain name of a call target: ``open_page``, ``chrome_cdp.open_page``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _reaches_open_page(
    node: ast.AST,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> bool:
    """Does this expression enter ``open_page``, directly or through a local function?"""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _called_name(child.func)
        if name == "open_page":
            return True
        if name in functions and name not in seen:
            seen.add(name)
            if _reaches_open_page(functions[name], functions, seen):
                return True
    return False


def _bare_wait_for_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Every ``asyncio.wait_for`` whose awaited coroutine enters ``open_page``."""
    functions = _functions(tree)
    calls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "wait_for"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            continue
        if not node.args:
            continue
        if _reaches_open_page(node.args[0], functions, set()):
            calls.append((node.lineno, ast.unparse(node)))
    return calls


@pytest.mark.req("R-CDP-QUEUE-CREDIT.invariant")
def test_no_connector_bounds_a_browser_attempt_with_a_bare_wait_for():
    """R-CDP-QUEUE-CREDIT: a browser attempt must run under the shared helper.

    A bare ``asyncio.wait_for`` around a coroutine that opens a page counts the
    seconds the attempt waits for a navigation permit against the attempt's own
    timeout — which is how Ozon and Avito answered "CDP timeout after 20.0s"
    in a full price comparison while both worked alone. Every connector that
    opens a page must bound that attempt with ``bounded_attempt`` instead.
    """
    offenders: list[str] = []
    for spec in SOURCES:
        module = importlib.import_module(spec.module)
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, source in _bare_wait_for_calls(tree):
            offenders.append(f"{spec.name} ({path.name}:{lineno}): {source}")
    assert not offenders, "browser attempts bounded with a bare asyncio.wait_for:\n" + "\n".join(offenders)
