# Examples

Runnable scripts that exercise the connectors directly, without an MCP client. Handy
for verifying an install, or as a starting point for your own automation.

All of them hit live marketplaces, so they need network access — and, for Ozon, a
Russian-friendly IP or the CDP tier.

```bash
uv run python examples/price_check.py "стиральная машина узкая"
uv run python examples/seller_lookup.py 5535522
uv run python examples/rating_breakdown.py "iphone 15"
uv run python examples/health_check.py
```

If a script reports a marketplace as blocked, that is expected from a datacenter IP —
see [docs/ANTI_BOT.md](../docs/ANTI_BOT.md).
