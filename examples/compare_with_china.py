#!/usr/bin/env python3
"""Compare a product across Russian marketplaces and Taobao side by side.

    uv run python examples/compare_with_china.py "iphone 15"

Taobao prices are in yuan and are NEVER ranked against rubles — this example
shows them in a separate block so a stale conversion can't fabricate a bargain.
Needs your Chrome (CDP) for Taobao, Avito and the other tier-2 sources.
"""

from __future__ import annotations

import asyncio
import sys

from compare_connector.server import compare_prices


async def main(query: str) -> int:
    result = await compare_prices(query=query, per_source_limit=5)
    print(f"Query: {result.query!r}  complete: {result.complete}\n")

    print("Per-source outcome:")
    for outcome in result.source_outcomes:
        marker = "ok " if outcome.status == "ok" else "!! "
        print(f"  {marker}{outcome.source:<15} {outcome.status:<14} {outcome.detail[:40]}")

    rub_offers = [o for o in result.offers if o.price_rub is not None]
    print(f"\nRuble offers, cheapest first ({len(rub_offers)}):")
    for offer in rub_offers[:10]:
        print(f"  {offer.source:<15} {offer.price_rub:>10,.0f} ₽  {offer.title[:44]}")

    yuan_note = [o for o in result.offers if o.source == "taobao"]
    if yuan_note:
        print(f"\nTaobao returned {len(yuan_note)} offer(s) — prices in yuan, not ranked.")
        print("Convert explicitly if you need them in rubles; see taobao-connector's SKILL.md.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "iphone 15")))
