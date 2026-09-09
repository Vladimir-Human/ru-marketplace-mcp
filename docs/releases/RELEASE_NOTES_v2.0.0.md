# v2.0.0

## Русский

Большой продуктовый релиз: marketplace MCP превращается из набора коннекторов в
evidence-aware decision layer.

- `compare_prices` возвращает raw и comparable winners, учитывает наличие и умеет проверить победителя через `compare_verify_offer`.
- `marketplace_sources.capabilities` даёт routing metadata до первого сетевого запроса.
- DSH skills используют progressive disclosure и дешёвый compare mount.
- Добавлены v2 research artifacts: identity, protocol, operations, security и eval matrix.
- Исправлена утечка upstream error text MPStats в ToolError.

Проверено: product/DSH/security tests, contract snapshot, offline suite, mypy, Ruff, CI matrix и release gates.

## English

Major product release: marketplace MCP becomes an evidence-aware decision layer rather than only a connector collection.

- `compare_prices` exposes raw and comparable winners, supports stock-aware ranking, and verifies a winner through `compare_verify_offer`.
- `marketplace_sources.capabilities` provides routing metadata before the first network call.
- DSH skills use progressive disclosure and the cheap compare mount.
- Added v2 research artifacts: identity, protocol, operations, security, and eval matrix.
- Fixed upstream MPStats error text leakage into ToolError.

Verified: product/DSH/security tests, contract snapshot, offline suite, mypy, Ruff, CI matrix, and release gates.
