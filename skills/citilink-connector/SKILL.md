---
name: citilink-connector
description: Use this skill when the operator needs Citilink electronics data — search or a product card. Trigger on "ситилинк", "citilink", "ситилинк цена". Always needs the operator's Chrome over CDP (Qrator rate block + gRPC-web). Skip for non-Citilink tasks.
---

# Citilink Connector

Reads Citilink inside the operator's Chrome over CDP. The whole domain answers
HTTP 429 (stricter than DNS), and its data transport is gRPC-web — a binary
protobuf protocol that is not worth reversing. A real Chrome passes Qrator and
renders the pages, so the connector reads the DOM instead.

## When to use
- Search Citilink by text
- One product's price, old price, availability

## Tools available
- `citilink_search(query)` — tiles from the rendered search page
- `citilink_card(product_url)` — one card. The URL carries `/product/<slug>/`,
  where the slug ends in a numeric id: `/product/noutbuk-lenovo-2169270/`.
- `citilink_selfcheck()` — tri-state canary

## Why the price used to come back null

Citilink renders the currency glyph in a **separate element** from the digits:

```html
<span data-meta-price="59990"><span>59 990</span><span>₽</span></span>
```

The old extractor filtered text lines for `руб|₽` and so never saw a line
carrying both. Title parsed, price did not — on every product.

## Read the data-meta attributes, not the class names

Citilink's class names are build-hashed (`app-catalog-51bw0j-…`) and change with
every deploy, so they are useless as selectors. The site publishes a stable
`data-meta-*` contract for its own analytics, and that is what the extractor
keys on:

| attribute | holds |
|---|---|
| `data-meta-name="Snippet__title"` | the product name anchor |
| `data-meta-name="Snippet__price"` | the current price (glyph in a child span) |
| `data-meta-name="Snippet__old-price"` | the strikethrough price, no glyph |
| `data-meta-price="59990"` | the exact amount, machine-readable |

`data-meta-price` needs no parsing and cannot be confused with an instalment or a
badge, so it is preferred; the display strings are the fallback.

If prices start coming back null, check whether those attributes still exist
before touching anything else — a class-name change is expected and harmless, a
`data-meta-*` change is the real drift.

## Numbers in a tile that are not the price

A tile also carries `- 10%` (discount badge), `в 1356 пунктов` (delivery points),
`(от 8 дней)` (delivery estimate), a rating and an opinion count. Only a number
attached to the currency glyph is treated as the price. Also note the **first
product anchor in a tile is an empty overlay link** — it has no text, so the
title comes from the `Snippet__title` anchor.

## Gotchas
- gRPC-web is deliberately not reversed: DOM extraction is the maintainable route.
- A green `citilink_selfcheck` proves the page rendered and the extractor returned
  a shape. It does not prove the numbers are right — for that, open the product
  page in the same Chrome and compare.
- From a datacenter address the domain answers 429, so nothing can be verified
  there. This connector only means anything from the operator's own machine.
