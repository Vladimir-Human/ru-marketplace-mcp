#!/usr/bin/env python3
"""Production structure gate and optional local T7 report.
START_MODULE_CONTRACT
PURPOSE: Gate import boundaries, measured size caps and pure modules in lint.
SCOPE: packages/*/src/**/*.py; --exempt waives only size overruns; only --report runs git.
DEPENDS: Standard-library argparse, ast, json, pathlib, subprocess; source registry data.
LINKS: M-ARCH-GATE, R-ARCH-GATE.
START_MODULE_MAP
  imports: Measure scoped def nodes and resolve registry-backed dynamic imports.
  main: Check measured sizes, baseline freshness, boundaries and purity.
  report: Print T7 counts and the non-release multi-package commit share.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

import argparse
import ast
import json
from pathlib import Path

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
BANNED = ("asyncio", "httpx", "fastmcp", "subprocess", "socket", "mcp_core.transport", "mcp_core.http")

# START_BLOCK_CHECKER


def imports(node, package, registry, env, scope=""):
    def resolve(value):
        if isinstance(value, ast.Name):
            return env.get(value.id, "")
        if isinstance(value, (ast.Attribute, ast.Subscript)):
            base = resolve(value.value)
            if base == "@registry" or (base == "mcp_core" and getattr(value, "attr", "") == "sources"):
                return "@registry"
            return f"{base}.import_module" if base and getattr(value, "attr", "") == "import_module" else ""
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Call) and ast.unparse(value.func) in {"list", "tuple"}:
            return resolve(value.args[0]) if value.args else ""
        return ""

    def bind(target, value):
        names = (n for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        env.update((n.id, value) for n in names)

    if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
        scope = f"{scope}.{node.name}" if scope else node.name
        if isinstance(node, FUNCTIONS):
            yield node, [scope], None
    if isinstance(node, (*FUNCTIONS, ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        helper = isinstance(node, FUNCTIONS) and env.get("_SELFCHECKS") == "@registry"
        helper = helper and node.name == "_run_one_selfcheck"
        if helper:
            env[node.name] = "@selfcheck"
        env = env.copy()
        if isinstance(node, FUNCTIONS):
            env.update((n.arg, "") for n in ast.walk(node.args) if isinstance(n, ast.arg))
            if helper:
                env["module_path"] = "@registry"  # Stop: existing registry-fed CLI helper.
        for generator in getattr(node, "generators", []):
            bind(generator.target, resolve(generator.iter))
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        base = getattr(node, "module", "") or ""
        if isinstance(node, ast.ImportFrom) and node.level:
            prefix = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
            base = ".".join(filter(None, (prefix, base)))
        names = [(base + "." if base else "") + a.name for a in node.names]
        for alias, name in zip(node.names, names, strict=True):
            value = name if alias.asname or isinstance(node, ast.ImportFrom) else name.split(".")[0]
            env[alias.asname or alias.name.split(".")[0]] = (
                "@registry" if value == "mcp_core.sources" or value.startswith("mcp_core.sources.") else value
            )
        yield node, names, True
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = resolve(node.value)
        # Stop: accept only an exact AST match to the registry's mount rows.
        if package == "marketplace_connector" and node.value and ast.dump(node.value) == registry:
            value = "@registry"
        for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
            bind(target, value)
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        bind(node.target, resolve(node.iter))
    elif isinstance(node, ast.Call):
        func = resolve(node.func)
        if func in {"importlib.import_module", "__import__", "@selfcheck"}:
            index = 1 if func == "@selfcheck" else 0
            arguments = node.args[index:] + [k.value for k in node.keywords if k.arg in {"name", "module_path"}]
            value = resolve(arguments[0]) if arguments else ""
            backed = value == "@registry" and (func != "__import__" or package == "marketplace_connector")
            yield node, ["" if value == "@registry" else value], not backed
    for child in ast.iter_child_nodes(node):
        yield from imports(child, package, registry, env, scope)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--exempt", action="store_true")
    parser.add_argument("--report", action="store_true")
    root = (args := parser.parse_args()).root.resolve()
    baseline = json.loads((root / "scripts/arch_baseline.json").read_text(encoding="utf-8"))
    files, functions, failures = {}, {}, []
    registry_path = root / "packages/mcp-core/src/mcp_core/sources.py"
    registry_nodes = ast.walk(ast.parse(registry_path.read_text(encoding="utf-8"))) if registry_path.exists() else []
    rows = [n for n in registry_nodes if isinstance(n, ast.Call) and ast.unparse(n.func) == "SourceSpec"]
    registry = tuple(
        (kw["mount"], kw["module"])
        for n in rows
        if (kw := {k.arg: ast.literal_eval(k.value) for k in n.keywords}).get("mount")
    )
    registry = ast.dump(ast.parse(repr(registry), mode="eval").body) if registry else None
    for source in sorted(root.glob("packages/*/src/**/*.py")):
        path = source.relative_to(root).as_posix()
        tree = ast.parse(text := source.read_text(encoding="utf-8"), filename=path)
        files[path] = (len(text.splitlines()), None)
        package = ".".join(source.relative_to(root).parts[3:-1])
        owner = next((p for p in package.split(".") if p == "mcp_core" or p.endswith("_connector")), "")
        for node, names, unbound in imports(tree, package, registry, {"__import__": "__import__"}):
            if unbound is None:
                functions[f"{path}::{names[0]}"] = (node.end_lineno - node.lineno + 1, node.lineno)
                continue
            for name in names:
                foreign = any(p.endswith("_connector") and p != owner for p in name.split("."))
                allowed = not unbound and owner in {"compare_connector", "marketplace_connector"}
                if (foreign or not name) and not allowed:
                    failures.append((f"{path}:{node.lineno}", "boundary", f"load {name or ast.unparse(node)}"))
                if path in baseline["pure"] and any(name == b or name.startswith(b + ".") for b in BANNED):
                    failures.append((f"{path}:{node.lineno}", "purity", f"forbidden import {name}"))
        for node in ast.walk(tree):
            if path in baseline["pure"] and isinstance(node, ast.Await):
                failures.append((f"{path}:{node.lineno}", "purity", "await in pure module"))
    for kind, measured, limit, slack in (("files", files, 1200, 100), ("functions", functions, 150, 20)):
        for key, (size, line) in measured.items():
            path = key.split("::")[0] + (f":{line}" if line else "")
            cap = baseline[kind].get(key, limit)
            if size > cap or (key in baseline[kind] and cap - size > slack):
                failures.append((path, "size" if size > cap else "slack", f"{key}: {size} lines, cap {cap}"))
        for key in baseline[kind].keys() - measured.keys():
            failures.append((key.split("::")[0], "missing", f"removed: {key}"))
    failures.extend((p, "missing", "pure-list module no longer exists") for p in baseline["pure"] if p not in files)
    for path, rule, detail in failures:
        print(f"{'warning: ' if args.exempt and rule == 'size' else ''}{path}: {rule}: {detail}")
    if args.report:
        report(root, files, functions)
    return int(any(not (args.exempt and rule == "size") for _, rule, _ in failures))


# END_BLOCK_CHECKER
# START_BLOCK_REPORT


def report(root, files, functions):
    import subprocess

    for label, measured, limit in (("files", files, 1200), ("functions", functions, 150)):
        oversized = {path: size for path, (size, _) in measured.items() if size > limit}
        print(f"{label} over {limit}: {len(oversized)}")
        for path, size in sorted(oversized.items()):
            print(f"  {path}: {size}")
        largest = max(measured, key=lambda key: measured[key][0], default=None)
        print(f"largest {label[:-1]}: {largest}: {measured[largest][0]}" if largest else f"largest {label[:-1]}: none")
    conformance = root / "packages/marketplace-connector/tests/conformance"
    tests = sum(
        isinstance(n, FUNCTIONS) and n.name.startswith("test_")
        for p in conformance.rglob("*.py")
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
    )
    print(f"conformance tests: {tests}")
    try:
        tags = subprocess.run(
            ["git", "tag", "--list", "v*", "--sort=-creatordate"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        if not tags:
            print("non-release changes touching >=3 packages: unavailable (no v* tag)")
            return
        history = subprocess.run(
            ["git", "log", "--format=%x1e%s", "--name-only", f"{tags[0]}..HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
        total = wide = 0
        for record in history.split("\x1e")[1:]:
            subject, *paths = record.splitlines()
            if subject.startswith("release:"):
                continue
            total += 1
            packages = {p.split("/")[1] for p in paths if p.startswith("packages/") and len(p.split("/")) >= 3}
            wide += len(packages) >= 3
        share = f"{wide / total:.1%}" if total else "0.0%"
        print(f"non-release changes touching >=3 packages: {wide}/{total} ({share}) since {tags[0]}")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"non-release changes touching >=3 packages: unavailable ({exc})")


# END_BLOCK_REPORT
if __name__ == "__main__":
    raise SystemExit(main())
