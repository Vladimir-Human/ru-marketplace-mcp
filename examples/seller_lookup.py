#!/usr/bin/env python3
"""Find out who actually sells a Wildberries product.

    uv run python examples/seller_lookup.py 5535522

A marketplace listing shows a trading name, which is easy to imitate. The
registered legal entity and its tax ids are not — which is how you tell an official
brand store from a reseller using a lookalike name.
"""

from __future__ import annotations

import asyncio
import sys

from wb_connector.server import wb_card, wb_seller


async def main(nm_id: int) -> int:
    card = await wb_card(nm_ids=[nm_id])
    if not card.items:
        print(f"No product found for nmId {nm_id}")
        return 1

    item = card.items[0]
    price = f"{item.price_rub:,.0f} RUB" if item.price_rub else "no price (likely delisted)"
    print(f"Product: {item.name}")
    print(f"Brand:   {item.brand}")
    print(f"Price:   {price}")
    print(f"Rating:  {item.review_rating} ({item.feedbacks} reviews)")
    print(f"Seller shown on the listing: {item.supplier}\n")

    if not item.supplier_id:
        print("This listing exposes no supplier id, so the legal entity cannot be resolved.")
        return 0

    seller = await wb_seller(supplier_id=item.supplier_id)
    print("Registered entity behind that name:")
    print(f"  Name:      {seller.name}")
    print(f"  Full name: {seller.full_name}")
    print(f"  Trademark: {seller.trademark or '—'}")
    print(f"  INN:       {seller.inn or '—'}")
    print(f"  OGRN:      {seller.ogrn or '—'}")
    print(f"  Address:   {seller.legal_address or '—'}")

    if seller.foreign_codes:
        print(f"  EAEU codes: {seller.foreign_codes}")

    return 0


if __name__ == "__main__":
    nm_id = int(sys.argv[1]) if len(sys.argv) > 1 else 5535522
    sys.exit(asyncio.run(main(nm_id)))
