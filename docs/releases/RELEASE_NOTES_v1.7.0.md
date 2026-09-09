# v1.7.0

## Русский

Продуктовый релиз для качества сравнения и DSH-routing:

- `compare_prices` добавляет `cheapest_comparable`, чтобы не выдавать аксессуар или восстановленный товар как безопасного победителя.
- `marketplace_sources` сообщает capabilities источников до сетевого вызова: CDP/login, валюта, text search и mounted state.
- DSH price-интенты маршрутизируются в дешёвый compare mount; full-only skills явно требуют `RU_MARKETPLACE_MCP_FULL=1`.
- Добавлен исследовательский отчёт `DEEP_RESEARCH_MARKETPLACE_MCP.md`.

Проверено: 1218 offline-тестов, 192 targeted product/DSH tests, public contract, mypy, Ruff и GitHub CI на всех матричных окружениях.

## English

Product release for comparison quality and DSH routing:

- `compare_prices` adds `cheapest_comparable`, so an accessory or refurbished listing is not presented as the safe winner.
- `marketplace_sources` reports capabilities before network calls: CDP/login, currency, text search, and mounted state.
- DSH price intents route to the cheap compare mount; full-only skills explicitly require `RU_MARKETPLACE_MCP_FULL=1`.
- Added `DEEP_RESEARCH_MARKETPLACE_MCP.md`.

Verified: 1218 offline tests, 192 targeted product/DSH tests, public contract, mypy, Ruff, and GitHub CI across the matrix.
