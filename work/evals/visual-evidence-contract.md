# Optional native-vision evidence

The marketplace server remains the source of structured price, stock, identity,
and provenance fields. A DSH client that has native vision may attach a screenshot
from the operator's browser to a follow-up verification prompt; the server does
not assume that every MCP client can accept images.

## Evidence contract

```json
{
  "kind": "visual_evidence",
  "source": "ozon",
  "url": "https://example.invalid/product",
  "captured_at": "2026-09-12T00:00:00Z",
  "fields": {
    "title": {"value": "...", "confidence": 0.0},
    "price_rub": {"value": null, "confidence": 0.0},
    "availability": {"value": "unknown", "confidence": 0.0},
    "variant": {"value": "unknown", "confidence": 0.0}
  },
  "blocked": false,
  "notes": []
}
```

The model must preserve `unknown` when the screenshot does not prove a field.
Visual evidence can corroborate a parser result, but it does not create an exact
MPN/GTIN match from a title or replace structured pagination. A visible CAPTCHA or
login wall is recorded as `blocked`; the client must not attempt to bypass it.

This contract is deliberately optional and client-side: native vision capability
is runtime-specific and is not silently assumed by the MCP server.

## Browser recovery direction (2026-09-12)

Current primary-source review:

| Project | Relevant documented capability | Application here |
|---|---|---|
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | Persistent profiles, isolated contexts, optional vision capability | Reuse the dedicated marketplace profile; choose images only when the host can deliver them to a vision-capable model. |
| [Browser Use human interaction](https://docs.browser-use.com/cloud/agent/human-in-the-loop) | Live browser interaction during an agent session | Keep completed results visible while one marketplace awaits action, then resume only that source. |

These are architecture references, not integrations or measured CAPTCHA success
rates. No external browser service, credentials, paid solver, or dependency is
enabled by this research. These projects are credited here for design references;
they are not represented as contributors of merged code.

The immediate product surface is the MCP client: show one recovery row per
affected source, its status, a short reason, and a retry action. Keep useful
offers available. A spinner that repeatedly re-runs every marketplace conceals
partial success and increases traffic. Native vision should inspect a shortlisted
card when text evidence is insufficient, preserving URL, time, selected variant,
and unknown values. Detect image-input support and actual screenshot-tool access
in the host at runtime; an MCP handshake alone does not prove either capability.

Implemented server contract: comparison outcomes preserve structured challenge
metadata, with no automatic retries. Taobao and Lamoda search use the new code;
other adapters can still return legacy transport failures. A subset retry uses
the existing `sources` argument and does not merge old observations on the server.

Remaining implementation: an opt-in, bounded browser handoff must retain only
the challenged owned tab, stop navigation by other tasks into that tab, expose
the same profile to the operator, expire abandoned handoffs, and resume once
after completion. Both Playwright and raw-CDP paths currently close temporary
tabs in `finally`; claiming exact-tab resume today would be incorrect. Test
success, cancellation, expiration, concurrent source queries, and process restart
before advertising seamless resume. Compare latency, requests, challenge rate,
and completion rate on the same task set before claiming an improvement over
the existing parser/CDP flow.

### Implementation constraints from the lifecycle audit

Challenge detection in Lamoda and Taobao currently runs after the page context
has closed. Move decoding and classification inside the owned context before
introducing retention; changing `finally` alone cannot retain the right page.
Keep handles scoped to the MCP session, provider, original input and CDP endpoint.
Resume must atomically claim the exact target ID, avoid `goto`, recheck the current
host policy, and retain the original expiry rather than extending it on every
retry. A retry in another session must not adopt a tab by matching its URL.

Ordinary connector cleanup hides the scraping-profile browser globally on Windows
and macOS. Active handoffs must suppress this hiding, and explicit handoff must
reveal the owned window; activating a target alone does not prove visibility.
Expiry, cancellation and shutdown may close only recorded owned targets. An
in-memory lease cannot guarantee cleanup after a hard process kill while Chrome
survives: document this limit and reject stale handles after restart rather than
closing unknown tabs. A concurrent-ownership fixture must use distinct target
IDs and interleaved page discovery, not a single reused fake target.

Blocked payloads must never enter the success cache: otherwise even a completed
browser challenge can keep returning the old failure until TTL expiry. This
prerequisite is covered by sequential failure/success/cache regression tests.

Live probe on 2026-09-12: `lamoda_search("кроссовки")` in the local scraping
Chrome returned `challenge_required`, `requires_user_action=true`, and zero
cache entries. This verifies real challenge detection and absence of a poisoned
cache; it does not verify challenge completion, product extraction, or resume.

The raw-CDP lifecycle prerequisite was also exercised against real local Chrome:
a local HTTP fixture created one owned target, and the target disappeared on
normal context exit. Injecting failure into `/json` discovery after real target
creation also removed the owned target. Pre-existing target IDs were unchanged
in both cases. This covers normal operation and one attachment failure; it is
not a guarantee of cleanup when Chrome itself becomes unreachable.
