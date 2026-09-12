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
