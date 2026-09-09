# v2 identity and offer evidence

Accessed 2026-09-09. Sources are primary/authoritative documentation or the benchmark authors' publication. Quotes are verbatim excerpts from the retrieved pages (HTML whitespace normalized).

1. **P1 — Schema.org Product** ([Product](https://schema.org/Product), Schema.org, live development vocabulary). Tier 1, specification.
   - Quotes: “Any offered product or service.” “Publishers should be aware that applications designed to use specific schema.org properties (e.g. ... `gtin13`, ...) will typically expect such data to be provided using those properties, rather than using the generic property/value mechanism.”
   - Supports: A normalized identity record should preserve typed identifiers and product attributes (brand, color, category, GTIN) rather than putting everything in an untyped bag.
   - Counterpoint/limit: Product is broad enough to include services; Schema.org is a vocabulary, not a marketplace truth or a guarantee that an identifier is valid.
   - Repo implication: Keep product/entity identity separate from seller offers; retain typed GTIN/MPN/brand fields and provenance, and treat free-text attributes as supplementary evidence.

2. **P2 — Schema.org ProductGroup** ([ProductGroup](https://schema.org/ProductGroup), Schema.org, live development vocabulary). Tier 1, specification.
   - Quotes: “A ProductGroup represents a group of Products that vary only in certain well-described ways, such as by size, color, material etc.” “While a ProductGroup itself is not directly offered for sale, the various varying products that it represents can be.”
   - Supports: Variant-aware identity must distinguish a family/group from sellable child variants; `hasVariant`, `isVariantOf`, `productGroupID`, and `variesBy` express that relationship.
   - Counterpoint/limit: The page labels ProductGroup a “new” area and says adoption/implementation feedback matters; marketplace feeds may omit or misuse the relationship.
   - Repo implication: Do not merge size/color variants solely by title; model a parent family plus variant-level identity and offer linkage, with confidence when the relationship is inferred.

3. **P3 — Schema.org Offer** ([Offer](https://schema.org/Offer), Schema.org, live development vocabulary). Tier 1, specification.
   - Quotes: “An offer to transfer some rights to an item or to provide a service.” “For GTIN-related fields, see Check Digit calculator and validation guide from GS1.”
   - Supports: Price and availability belong to an offer/seller context rather than the abstract product; Offer also has fields for `availability`, `condition`, `price`, `priceCurrency`, `seller`, and `shippingDetails`.
   - Counterpoint/limit: Schema.org's default `businessFunction` is sell, but Offer can represent lease, repair, rental, or other rights; absent explicit semantics, “offer” is not necessarily a retail sale.
   - Repo implication: Normalize each marketplace listing as an offer observation keyed to a product candidate, preserving seller, condition, currency, availability, timestamp, and shipping/return evidence.

4. **P4 — Google Merchant Center product data specification** ([Product data specification](https://support.google.com/merchants/answer/7052112), Google, accessed 2026-09-09). Tier 1, operational specification.
   - Quotes: “Google uses this data to match your products to the right queries.” “Incorrect, inaccurate, or missing product information can cause disapprovals, limited eligibility, incorrect displays for your products.”
   - Supports: Identity matching depends on complete, correctly formatted product data, including variant attributes and identifiers; bad data can produce incorrect displays, not merely lower ranking.
   - Counterpoint/limit: This is Google's ad/free-listing ingestion policy and is not a neutral universal marketplace schema; its requirements and eligibility rules can change.
   - Repo implication: Validate incoming records before matching; retain raw source fields so a rejected/ambiguous match can be explained and replayed.

5. **P5 — Google Merchant Center GTIN and MPN guidance** ([GTIN attribute](https://support.google.com/merchants/answer/6324461), Google, accessed 2026-09-09). Tier 1, operational specification.
   - Quotes: “GTIN is ideally provided in combination with MPN.” “Only provide a GTIN if you’re sure it's correct. When in doubt don’t provide a GTIN (for example, don’t guess or make up a value).”
   - Supports: Prefer manufacturer identifiers for identity resolution, but never synthesize a GTIN; GTIN validation/check digit and variant-specific identifiers are explicit requirements. The page also states: “Each product and variant of a product (different colors or sizes) has its own GTIN.”
   - Counterpoint/limit: Many private-label/store-brand products have no GTIN, and Google says missing GTIN can make accurate matching impossible; therefore identifier-only matching has unavoidable coverage gaps.
   - Repo implication: Implement deterministic GTIN normalization/check-digit validation and MPN+brand matching, but allow a scored attribute/title match with an explicit “unverified/no-GTIN” state.

6. **P6 — Google Merchant Center Shipping attribute** ([Shipping](https://support.google.com/merchants/answer/6324484), Google, accessed 2026-09-09). Tier 1, operational specification.
   - Quotes: “The shipping [shipping] attribute lets you provide shipping speed and cost for a product.” “Shipping speed includes both handling time ... and transit time.”
   - Supports: Availability and price comparison alone are incomplete: shipping cost, destination, handling time, and transit time are separate offer facts and can override account defaults for bulky/fragile products.
   - Counterpoint/limit: The document describes Google's programs and country-specific obligations; shipping data may be account-level or absent from a listing, and displayed estimates are not a universal delivery guarantee.
   - Repo implication: Keep shipping cost/speed/destination as structured, timestamped offer fields; never fold shipping into product identity or compare headline prices without stating shipping assumptions.

7. **P7 — Web Data Commons Product Matching benchmark** ([WDC Products: A Multi-Dimensional Entity Matching Benchmark](https://arxiv.org/abs/2301.09521), Ralph Peeters, Reng Chiz Der, Christian Bizer, 2023). Tier 1, peer-reviewed-style arXiv research paper by benchmark authors.
   - Quotes (abstract): “The difficulty of an entity matching task depends on a combination of multiple factors such as the amount of corner-case pairs, the fraction of entities in the test set that have not been seen during training, and the size of the development set.” “WDC Products is a multi-dimensional benchmark for product matching that allows researchers to assess the robustness of their matching approaches.”
   - Supports: Product matching quality cannot be summarized by one easy split or one aggregate score; evaluation should include corner cases, unseen entities, and dataset-size/development effects. The companion WDC site reports a gold standard and large-scale product-offer corpora.
   - Counterpoint/limit: Benchmark results are not evidence that any particular matcher is correct for Russian marketplaces; domain language, attribute quality, and marketplace policies differ.
   - Repo implication: Build an offline fixture/gold set with hard variant, missing-ID, seller-brand, and unseen-product cases; report precision/recall by slice and abstain below a match threshold instead of forcing a merge.

## Cross-source synthesis for v2

The sources converge on a two-layer model: a durable product/family entity with typed identifiers and variant relationships (P1–P2), plus time-varying seller offers with price, condition, availability, shipping, and policy context (P3, P6). Google’s identifier guidance adds a crucial safety rule: invalid or guessed GTINs poison matching, while missing GTINs are normal for some products (P5). Therefore v2 should preserve raw evidence and provenance, validate identifiers, score multi-field candidates, and expose uncertainty/abstention. The WDC benchmark warns that aggregate matching metrics hide corner cases and unseen entities (P7); acceptance tests should be slice-based and include negative pairs.

## Retrieval limits

GS1’s public GTIN page returned HTTP 403 from this environment, so GS1 was used only through the direct GS1 validation references exposed by Schema.org/Google; no GS1 page is claimed as directly quoted. No live marketplace calls were made.
