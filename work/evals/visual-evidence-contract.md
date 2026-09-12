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
