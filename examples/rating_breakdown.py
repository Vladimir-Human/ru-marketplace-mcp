#!/usr/bin/env python3
"""Show what a Yandex Market star rating is actually made of.

    uv run python examples/rating_breakdown.py "iphone 15"

An average hides its own shape. 4.8 built from 502 five-star and 10 one-star
ratings is a good product; 4.8 with a fat one-star bucket is a product with a
recurring defect. The distribution is the interesting part.
"""

from __future__ import annotations

import asyncio
import sys

from yandex_connector.server import yandex_card, yandex_search


async def main(query: str) -> int:
    search = await yandex_search(query=query, limit=5)
    if not search.items:
        print(f"Nothing found for {query!r}")
        return 1

    print(f"Search: {search.returned} of {search.total_available} results\n")
    for item in search.items:
        price = f"{item.price_rub:,.0f}" if item.price_rub else "n/a"
        print(f"  [{item.product_id}] {item.title[:44]}")
        print(f"      {price:>10} RUB   rating {item.rating} ({item.rating_count} ratings)   {item.seller[:22]}")

    # Take the first result that actually has ratings — resale offers often have none.
    rated = next((i for i in search.items if i.rating_count), search.items[0])
    print(f"\nBreaking down [{rated.product_id}]…\n")

    card = await yandex_card(product_id=rated.product_id, include_reviews=True)

    print(f"{card.title[:60]}")
    print(f"  Everyday price:    {card.price_rub:,.0f} RUB" if card.price_rub else "  Everyday price: n/a")
    if card.price_with_plus:
        print(f"  With subscription: {card.price_with_plus:,.0f} RUB  (requires Yandex Plus)")
    print(f"  Offers from other sellers: {card.offers_count or '—'}")
    print(f"  Rating: {card.rating} from {card.rating_count} ratings, {card.review_count} written reviews\n")

    if card.rating_stars:
        total = sum(card.rating_stars.values()) or 1
        print("  Distribution:")
        for star in (5, 4, 3, 2, 1):
            count = card.rating_stars.get(star, 0)
            bar = "#" * round(40 * count / total)
            print(f"    {star}* {count:>6}  {bar}")
    else:
        print("  No distribution available for this offer.")

    if card.reviews:
        print(f"\n  Sample of {len(card.reviews)} server-rendered reviews:")
        for review in card.reviews[:3]:
            print(f"    {review.rating}* {review.author[:20]} ({review.date})  +{review.votes_up}/-{review.votes_down}")
            if review.pros:
                print(f"       + {review.pros[:64]}")
            if review.cons:
                print(f"       - {review.cons[:64]}")

    for warning in card.meta.warnings:
        print(f"\n  NOTE: {warning}")

    return 0


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "iphone 15"
    sys.exit(asyncio.run(main(query)))
