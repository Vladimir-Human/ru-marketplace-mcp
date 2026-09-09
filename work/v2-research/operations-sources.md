# v2.0.0 operations and evaluation sources

Collected 2026-09-09 by direct HTTP retrieval. These are operational design inputs for `ru-marketplace-mcp`; they are not claims that the repository currently implements every recommendation.

## 1. Playwright auto-waiting and actionability

- **URL:** https://playwright.dev/docs/actionability
- **Date accessed:** 2026-09-09
- **Tier:** 1 (official Playwright documentation)
- **Extract:** “Playwright performs a range of actionability checks on the elements before making actions… It auto-waits for all the relevant checks to pass and only then performs the requested action.” The page also says that failure to satisfy required checks within the timeout raises `TimeoutError`, and that assertions auto-retry.
- **Implication:** Browser extraction steps should be expressed as observable readiness conditions (visible/enabled/stable/receiving events) with bounded timeouts, rather than fixed sleeps. A timeout is a typed operational outcome to record per source/selector and feed into fallback and drift metrics. `force` should remain exceptional because it disables non-essential checks and can mask page drift.
- **Caveat:** Actionability proves that an interaction can be performed; it does not prove that the resulting product data is complete or semantically correct.

## 2. Playwright Trace Viewer

- **URL:** https://playwright.dev/docs/trace-viewer
- **Date accessed:** 2026-09-09
- **Tier:** 1 (official Playwright documentation)
- **Extract:** “Trace Viewer is a GUI tool that helps you explore recorded Playwright traces after the script has run.” The documentation says traces are useful for debugging failures on CI; the hosted viewer loads a `trace.zip` entirely in the browser and “does not transmit any data externally.”
- **Implication:** A failure or suspicious extraction sample should retain a bounded trace artifact (or an equivalent redacted diagnostic) keyed by run/source/attempt. This gives operators replayable evidence for anti-bot challenges, selector drift, and timing failures without sending marketplace data to a third party.
- **Caveat:** Traces can contain sensitive request/response data; retention, redaction, size limits, and access control must be explicit before enabling them in production.

## 3. Chrome DevTools Protocol Network domain

- **URL:** https://chromedevtools.github.io/devtools-protocol/tot/Network/
- **Date accessed:** 2026-09-09
- **Tier:** 1 (official Chrome DevTools Protocol specification)
- **Extract:** The Network domain documents `Network.enable` and the `Network.requestWillBeSent` / `requestWillBeSentExtraInfo` events, including request metadata and extra information associated with a request. It also documents that network instrumentation is enabled before issuing runtime commands in relevant debugging flows.
- **Implication:** The extractor can distinguish page/render failures from transport failures by recording request lifecycle events, status, redirects, content type, and timing. These signals support fallback selection (rendered browser, direct endpoint, cached response) and a drift detector that notices a changed API route or an unexpected challenge response.
- **Caveat:** CDP is browser/version-sensitive and network events do not by themselves establish that a response contains the intended catalog record; validate payload shape and identity independently.

## 4. WebDriver BiDi network events

- **URL:** https://www.w3.org/TR/webdriver-bidi/
- **Date accessed:** 2026-09-09
- **Tier:** 1 (W3C Recommendation/standard-track specification)
- **Extract:** The specification defines a bidirectional WebDriver protocol and modules for browser interaction and network events. The network module exposes request/response lifecycle information for automation clients.
- **Implication:** Keep the browser-control boundary abstract enough to support standards-based BiDi as well as CDP. A v2 evaluation should test the same extraction contract against the selected browser adapter and assert that request/response diagnostics remain available when the transport changes.
- **Caveat:** The specification is broad and implementation support varies by browser and client; treat feature availability as a capability probe, not an assumption.

## 5. OpenTelemetry observability primer

- **URL:** https://opentelemetry.io/docs/concepts/observability-primer/
- **Date accessed:** 2026-09-09
- **Tier:** 1 (official OpenTelemetry documentation)
- **Extract:** The primer presents observability through telemetry signals including traces, metrics, and logs, and describes OpenTelemetry as a vendor-neutral framework for generating, collecting, and exporting telemetry. The docs explicitly separate signals while describing how they can be correlated.
- **Implication:** Each marketplace extraction should have a trace/span carrying source, query, adapter/fallback, attempt, and outcome attributes; counters/histograms should cover success, empty-result, challenge, parse-error, latency, and item counts; logs should carry the same correlation ID. This permits per-source SLOs and forensic diagnosis without relying on log scraping.
- **Caveat:** High-cardinality attributes such as raw query text, URLs, or product IDs can make metrics expensive and leak data. Put them in sampled traces/logs or normalize/hash them; keep bounded dimensions in metrics.

## 6. OpenTelemetry traces

- **URL:** https://opentelemetry.io/docs/concepts/signals/traces/
- **Date accessed:** 2026-09-09
- **Tier:** 1 (official OpenTelemetry documentation)
- **Extract:** The page defines a trace as the path of a request through an application and describes spans as units of work with timing and attributes/events. Parent-child relationships provide a view of the complete operation.
- **Implication:** Model one root `extract` span with child spans for navigation, challenge detection, fallback attempts, parsing, identity matching, and persistence. A benchmark gate can then assert both end-to-end latency and that failed attempts expose a classified child span rather than an opaque timeout.
- **Caveat:** Span attributes are diagnostic evidence, not a substitute for domain acceptance checks such as matching the requested product and rejecting partial records.

## 7. Google SRE: Service Level Objectives

- **URL:** https://sre.google/sre-book/service-level-objectives/
- **Date accessed:** 2026-09-09
- **Tier:** 1 (Google SRE book, official publication)
- **Extract:** Google defines an SLI as “a carefully defined quantitative measure of some aspect of the level of service that is provided” and an SLO as the desired value for that measure. The chapter notes that a proxy may be needed when the ideal user-relevant measure is hard to obtain.
- **Implication:** Define SLIs around user-visible extraction quality: valid-record success rate, completeness/acceptance rate, challenge rate, p50/p95 latency, and freshness. Set source-specific SLOs and an error budget; use budget burn to decide when to disable an unstable route or require operator review. Measure accepted records, not merely HTTP 200 responses.
- **Caveat:** SLO targets are product decisions and need a representative fixture mix; a single aggregate target can hide one marketplace or query class failing systematically.

## 8. SoMaJo: State-of-the-art tokenization for German web and social media texts

- **URL:** https://aclanthology.org/W16-2607/
- **Date accessed:** 2026-09-09
- **Tier:** 1 (ACL Anthology peer-reviewed computational-linguistics paper)
- **Extract:** The ACL record identifies the paper as work on tokenization for German web and social-media text and provides the peer-reviewed publication record and accompanying paper PDF. Its subject demonstrates that web text requires language- and genre-aware normalization rather than assuming clean prose.
- **Implication:** Treat extraction normalization as a measurable stage: preserve raw text, normalize locale-specific numbers/units, and test titles, prices, ratings, and seller strings against multilingual and noisy fixtures. Record parse confidence and reject records when normalization changes identity-bearing fields unexpectedly.
- **Caveat:** This is a foundational language-processing source (2016), not a marketplace-specific benchmark; use it for the normalization principle, not current anti-bot behavior or performance claims.

## Operational synthesis for v2.0.0

1. Use bounded, observable browser actions and classify timeout/challenge/parse outcomes; do not equate a successful navigation with a valid record.
2. Instrument every attempt with correlated traces, metrics, and logs. Keep metric labels bounded and put sensitive/high-cardinality evidence in controlled traces or redacted artifacts.
3. Implement fallback as an explicit state machine whose transitions are driven by evidence (network status, challenge markers, schema/identity validation), with a maximum attempt budget.
4. Define benchmark gates on accepted-record correctness, completeness, p50/p95 latency, challenge rate, fallback recovery, and drift detection. Track these per source and fixture class, then aggregate only after stratification.
5. Retain replayable diagnostics for failures and drift, with redaction and retention limits. A drift alarm should fire on schema/selector changes and on sustained SLO/error-budget burn, not on one transient timeout.

