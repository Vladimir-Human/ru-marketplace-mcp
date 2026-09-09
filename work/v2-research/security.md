# v2.0.0 Security / privacy research

Date: 2026-09-09  
Scope: read-only repository review at commit `5f34b90c2583e48b03dc6d1985d7a8ffb05626ce`, plus offline synthetic probes. No live authenticated/private marketplace calls were made. The workspace already had unrelated uncommitted v1.9.0 changes; this report does not modify implementation.

## Executive findings

| ID | Severity | Confidence | Finding |
|---|---|---|---|
| S1 | High | Confirmed by synthetic probe | MPStats inner error path redacts the log but returns upstream message verbatim in the MCP ToolError. A secret or sensitive upstream error is therefore exposed to the model/client transcript. |
| S2 | High in shared deployment | Confirmed by design/code | HTTP transports have no built-in authentication or tenant isolation. Any reachable listener can invoke every exposed read tool; CDP-backed services additionally act through the operator's browser session. A warning is not an access control. |
| S3 | High for CDP deployments | Code-reviewed, needs regression test | `open_page` checks only that the initial URL starts with `http://` or `https://`; browser navigation follows redirects, and no final-host policy is enforced before extractor JavaScript runs. A redirect/open-redirect can move a connector's browser tab to an untrusted or internal host (SSRF / data integrity boundary). |
| S4 | Medium | Confirmed by code/docs mismatch | Megamarket resolves `/profileService/address/list` via `credentials: include` and caches the resulting address ID; `SECURITY.md` says only MPStats enters a private/account-gated zone. The privacy model and user disclosure are therefore inaccurate. |
| S5 | Medium | Code-reviewed | Raw CDP fallback uses `websockets.connect(..., max_size=None)` while enabling Network events. A hostile/huge CDP event can consume unbounded memory before the post-evaluation body cap applies. |

## S1 — MPStats error redaction bypass (confirmed)

Evidence: `packages/mpstats-connector/src/mpstats_connector/server.py:357-359`.
The `inner_code != 200` branch correctly logs `_redact(str(data.get("message", "")))`, but constructs `TransportDownError` with the unredacted `data.get("message", "")`:

```python
log_event(..., message=_redact(str(data.get("message", ""))))
raise_tool_error(TransportDownError(f"mpstats code {inner_code}: {data.get('message', '')}", provider="mpstats"))
```

Reproduction (offline and synthetic only): patch `_cookie_header`, `_client`, `_post_json_budgeted`, and `_polite_wait` in memory; return an upstream JSON body with `{"code":500,"message":"upstream rejected Cookie: mp_auth=synthetic-session-secret"}`. The stderr event contains `Cookie: <redacted>`, while the resulting ToolError JSON contains `synthetic-session-secret` (`synthetic_secret_in_tool_response=true`). No real token was read or sent.

Impact: upstream-controlled text can disclose credentials, request details, or personal data to the model and MCP client transcript.  
Mitigation: apply `_redact` to the message before constructing *every* ConnectorError, including inner-code, HTTP body previews, parser previews, and Megamarket/other upstream-derived error text. Add a parameterized test asserting the secret is absent from both logs and `ToolError` for inner-code failures. Add a central `raise_redacted_tool_error` helper or error-constructor boundary to prevent future misses.

## S2 — unauthenticated HTTP and no tenant boundary

Evidence: `mcp_core/runtime.py:203-223` explicitly states servers have no built-in auth and only logs `http_bind_exposed`; `run_server` still starts on any configured routable host. `docs/DEPLOYMENT.md` describes auth as an operator reverse-proxy responsibility. `docker-compose.yml` intentionally binds inside containers to `0.0.0.0` and relies on host-loopback port publishing.

Impact: a shared host, accidentally broad port publish, or reverse-proxy misconfiguration lets any network caller invoke tools, consume proxies/quotas, and use the operator's authenticated CDP browser. This is especially material for the unified server and MPStats. MCP's security specification also says local servers SHOULD bind localhost and SHOULD implement proper authentication for all connections.

Mitigation options for v2: provide an opt-in bearer/API-key middleware at the MCP layer; fail closed when a non-loopback bind is configured without an explicit auth mechanism; add per-tenant process/profile isolation for CDP and MPStats; include a deployment self-test that rejects public binds without auth. At minimum expose a machine-readable `auth_required` posture and document that the HTTP endpoint is single-tenant only.

Boundary: this is not an exploit when an operator deliberately runs loopback-only stdio or a correctly firewalled loopback-published compose stack. It is a high-impact deployment hazard for shared/routable HTTP.

## S3 — final-host redirect / browser SSRF boundary

Evidence: `mcp_core/transport/chrome_cdp.py:706-730` performs only a prefix scheme check and delegates navigation. `_playwright_page` calls `page.goto(url, ...)`; Playwright follows normal HTTP redirects. `_raw_cdp_page.goto_and_status` sends `Page.navigate` and accepts the final page; both paths yield to connector extractors without a final-host check. The docstring explicitly delegates host allowlisting to callers, but does not define redirect handling.

The initial URLs are mostly connector constants, which lowers exploitability. However, upstream redirects and open redirects are outside the caller's initial allowlist. A compromised marketplace edge or crafted redirect can make the operator's browser navigate to an internal/routable host; the extractor then processes attacker-authored DOM and the tab runs with the scraping profile's credentials. This is a security/data-integrity boundary even where cookies are origin-scoped.

Mitigation: make `open_page` accept an explicit allowed-host set/predicate and enforce it on `page.url` after navigation and before yielding; reject non-HTTPS for CDP unless a connector explicitly needs HTTP; optionally intercept `page.on("request")` / raw `Network.requestWillBeSent` to block cross-host redirects and private IP ranges. Add tests for 30x-to-off-host, scheme-relative, userinfo, IDN/punycode, and private-IP targets. Keep connector constants as the source of allowed hosts.

Boundary: no live redirect was attempted; this is a code-path finding requiring a regression fixture/probe before calling it exploitable against a specific marketplace.

## S4 — Megamarket private profile data is outside the documented model

Evidence: `packages/megamarket-connector/src/megamarket_connector/server.py:228-264` calls `/profileService/address/list` from the authenticated Chrome context (`credentials: 'include'`) before search, chooses the default address, and stores `_address_id` globally. `SECURITY.md` currently says MPStats is the one exception that enters an account-gated zone.

Impact: users may enable Megamarket believing only public catalog reads occur, while the server reads location/profile data and uses it to personalize results. Global module state also assumes one browser profile / tenant per process; changing the attached profile without restart can reuse stale address state.

Mitigation: update SECURITY.md and tool docs; make profile-address lookup explicit opt-in (`MEGAMARKET_USE_PROFILE_ADDRESS=false` by default), or return a clear privacy notice in metadata before it is used. Scope the cache to a profile/session identity (or disable it) and expose `address_source=profile|configured|none` without returning raw address identifiers. Add tests proving no profile endpoint is called in public mode and that cache invalidates when profile changes.

## S5 — raw CDP websocket unbounded frame size

Evidence: `chrome_cdp.py:658` and browser-level connect use `max_size=None`; `Network.enable` is called at lines 661-663. The extractor result is capped only after `Runtime.evaluate` returns, so the cap does not protect the websocket/event channel.

Impact: a compromised/misconfigured CDP endpoint or unusually large event can allocate unbounded memory and hang/kill the connector. This is mostly an operator/sidecar trust issue, but becomes relevant when `CHROME_CDP_HOST` points across a container/network boundary.

Mitigation: set a finite websocket `max_size` (for example a tested 8–16 MiB envelope), avoid `Network.enable` unless needed, cap/ignore oversized event payloads, and surface a bounded transport error. Add a synthetic oversized-frame test with a fake websocket.

## Prompt injection and external content

Product titles, seller names, and reviews are untrusted seller/buyer-authored text. Existing `SECURITY.md` and skill docs warn the consuming agent, but this is guidance rather than an enforceable protocol boundary. For v2, add explicit provenance fields (for example `content_trust="external_untrusted"`) and a stable wrapper/annotation around free-form text. Add evaluation fixtures containing instruction-like reviews (“ignore prior instructions”, URLs, tool-call bait) and assert the agent/router never treats them as policy or executes external actions. Do not attempt to silently sanitize or rewrite marketplace text; preserve it as data while labeling it.

## Official sources consulted

1. **MCP Transports, 2025-06-18 specification** — <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports> (retrieved 2026-09-09). The security section states: “When running locally, servers SHOULD bind only to localhost (127.0.0.1) rather than all network interfaces (0.0.0.0)” and “Servers SHOULD implement proper authentication for all connections”; it warns that otherwise DNS rebinding can let remote websites interact with local MCP servers. This directly supports S2's fail-closed/auth recommendation.
2. **OWASP Server-Side Request Forgery Prevention Cheat Sheet / SSRF overview** — <https://owasp.org/www-community/attacks/Server_Side_Request_Forgery> (retrieved 2026-09-09). The page describes attackers using server-side requests to reach cloud metadata at `169.254.169.254`, internal database HTTP interfaces, and internal REST services; this supports treating browser-followed off-host redirects as an SSRF/private-network boundary even when the initial URL is allowlisted.
3. **OWASP GenAI LLM01: Prompt Injection** — <https://genai.owasp.org/llmrisk/llm01-prompt-injection/> (retrieved 2026-09-09). The mitigation text recommends assessing context relevance/groundedness, least privilege, human approval for high-risk actions, and to “separate and identify external content.” This supports provenance labels and prompt-injection evals.
4. **Chrome DevTools remote debugging documentation** — <https://developer.chrome.com/docs/devtools/remote-debugging> (retrieved 2026-09-09). The documentation shows the CDP HTTP surfaces at `http://localhost:9222/json` and `/json/version`, confirming that the debug endpoint exposes browser targets and a browser websocket; this supports treating any non-loopback `CHROME_CDP_HOST` as a privileged control-plane boundary.
5. **OWASP Logging Cheat Sheet** — <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html> (retrieved 2026-09-09). Apply its guidance to avoid logging secrets and sensitive personal data and to treat logs as a security-relevant data store. The report's S1 probe demonstrates that a redacted log alone is insufficient when the same upstream text is returned in a tool error.

## v2 security acceptance/evaluation plan

- **Secret non-disclosure:** property-test every ConnectorError path with synthetic bearer/JWT/cookie/query/proxy secrets; assert absence from logs *and* serialized MCP tool errors.
- **HTTP posture:** start every transport on loopback and non-loopback in a fixture; assert non-loopback requires configured auth or fails closed; probe unauthenticated initialize/tools/call.
- **CDP host/redirect:** fake CDP navigation responses for 30x to off-host, private-IP, alternate scheme, IDN, and userinfo targets; assert block before extractor evaluation.
- **Tenant isolation:** run two isolated profiles/tokens concurrently; assert caches, CDP contexts, proxy settings, and responses cannot cross-contaminate.
- **Oversized protocol input:** send oversized CDP frames/body/events and assert bounded memory/timeout plus classified error.
- **Prompt injection:** replay hostile product/review fixtures through compare and unified routes; assert provenance survives and routing never executes instructions from content.
