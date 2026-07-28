#!/usr/bin/env python3
"""Search Avito classifieds and show one seller's reputation.

    uv run python examples/avito_search.py "thinkpad x1"

From a datacenter IP this falls back to your Chrome (CDP); keep the scraping
profile logged into avito.ru. Note how priceless ads surface as None, not 0.
"""

from __future__ import annotations

import asyncio
import sys

from avito_connector.server import avito_search, avito_seller


async def main(query: str) -> int:
    result = await avito_search(query=query, page=1)
    print(f"Query: {result.query!r}  tier: {result.tier_used}  total: {result.total_count}\n")
    for item in result.items[:10]:
        price = f"{item.price_rub:,.0f} ₽" if item.price_rub else "цена не указана"
        print(f"  {price:>16}  {item.title or ''}  — {item.location or ''}  ({item.seller_name or '?'})")

    first_with_seller = next((i for i in result.items if i.seller_id), None)
    if first_with_seller:
        seller = await avito_seller(seller_id_or_url=first_with_seller.seller_id)
        if seller.seller:
            print(
                f"\nSeller '{seller.seller.name}': rating {seller.seller.rating_score} "
                f"({seller.seller.rating_count} reviews), {seller.active_items} active listings"
            )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ноутбук")))
