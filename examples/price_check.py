#!/usr/bin/env python3
"""Compare a product's price across every available marketplace.

    uv run python examples/price_check.py "стиральная машина узкая"

Demonstrates the pattern that matters most when reading a comparison: check
`complete` and `source_outcomes` before quoting a "cheapest" price, because a
blocked marketplace is not an absent one.
"""

from __future__ import annotations

import asyncio
import sys

from compare_connector.server import compare_prices, compare_sources


async def main(query: str) -> int:
    sources = await compare_sources()
    print(f"Marketplaces available: {', '.join(sources['searchable']) or 'none'}\n")

    result = await compare_prices(query=query, per_source_limit=5)

    print(f"Query: {result.query!r}")
    print(f"Offers: {result.total_offers}   Complete: {result.complete}\n")

    print("Per-source outcome:")
    for outcome in result.source_outcomes:
        marker = "ok " if outcome.status == "ok" else "!! "
        print(f"  {marker}{outcome.source:<15} {outcome.status:<14} {outcome.elapsed_ms:>6}ms  {outcome.detail[:44]}")

    print("\nOffers, cheapest first:")
    for offer in result.offers[:12]:
        price = f"{offer.price_rub:,.0f}" if offer.price_rub else "no price"
        plus = ""
        if offer.price_with_subscription_rub:
            plus = f"  (with subscription: {offer.price_with_subscription_rub:,.0f})"
        print(f"  {offer.source:<15} {price:>12} RUB{plus}  {offer.title[:40]}")

    if result.cheapest:
        print(f"\nCheapest: {result.cheapest.source} at {result.cheapest.price_rub:,.0f} RUB")
        if result.price_spread_rub:
            print(f"Spread between highest and lowest: {result.price_spread_rub:,.0f} RUB")

    # The honest caveat, printed rather than buried.
    if not result.complete:
        print("\nNOTE: some marketplaces did not answer, so this ranking is partial.")
        for warning in result.warnings:
            print(f"  - {warning}")

    return 0


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "стиральная машина узкая"
    sys.exit(asyncio.run(main(query)))
