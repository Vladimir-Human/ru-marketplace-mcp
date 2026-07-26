#!/usr/bin/env python3
"""Run every connector's selfcheck and summarise what works from here.

    uv run python examples/health_check.py

Verdicts are tri-state and the difference matters:

  success        every endpoint family answered in the expected shape
  drift_detected reachable but no longer parseable -> the connector needs updating
  inconclusive   transport, geo block or captcha -> says nothing about the parsers

From a datacenter IP, Ozon reporting `inconclusive` is normal and expected.
"""

from __future__ import annotations

import asyncio
import sys


async def run_one(name: str, coro) -> tuple[str, str, dict]:
    try:
        result = await asyncio.wait_for(coro, timeout=180)
    except Exception as exc:
        return name, "unreachable", {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    checks = {}
    raw_checks = getattr(result, "checks", {}) or {}
    for key, entry in raw_checks.items():
        state = getattr(entry, "state", None) or (entry.get("state") if isinstance(entry, dict) else "?")
        checks[key] = state
    return name, result.status, checks


async def main() -> int:
    from detmir_connector.server import detmir_selfcheck
    from wb_connector.server import wb_selfcheck
    from yandex_connector.server import yandex_selfcheck

    probes = [
        ("wildberries", wb_selfcheck()),
        ("yandex_market", yandex_selfcheck()),
        ("detsky_mir", detmir_selfcheck()),
    ]

    try:
        from ozon_connector.server import ozon_selfcheck

        probes.append(("ozon", ozon_selfcheck()))
    except Exception:
        print("ozon-connector is not installed; skipping\n")

    results = await asyncio.gather(*(run_one(name, coro) for name, coro in probes))

    print(f"{'connector':<16} {'status':<16} per-endpoint")
    print("-" * 72)
    for name, status, checks in results:
        detail = ", ".join(f"{k}={v}" for k, v in checks.items()) or "—"
        print(f"{name:<16} {status:<16} {detail}")

    healthy = sum(1 for _, status, _ in results if status == "success")
    print(f"\n{healthy} of {len(results)} connectors fully healthy from this network.")

    if any(status == "drift_detected" for _, status, _ in results):
        print("A drift_detected verdict means a marketplace changed its payload shape.")
    if any(status == "inconclusive" for _, status, _ in results):
        print("An inconclusive verdict usually means geo blocking, not a code bug.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
