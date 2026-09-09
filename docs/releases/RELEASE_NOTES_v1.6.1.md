# v1.6.1

## Русский

Patch-релиз с обновлениями доставки и диагностики:

- CDP-поиск Taobao и Lamoda отличает антибот-проверку с HTTP 200 от настоящего parser drift и сообщает `inconclusive/blocked`.
- FastMCP ограничен совместимой веткой `>=3.4.6,<4`; FastMCP 4 меняет публичную MCP-схему `_meta` на `meta`.
- Обновлены GitHub Actions для Docker publishing и Node.js CI.
- Обновлены зависимости workspace и документация по anti-bot поведению Lamoda.

Проверено: 1213 офлайн-тестов, public contract snapshot, mypy, Ruff и 14/14 реальных stdio MCP-сессий.

## English

Patch release with delivery and diagnostics updates:

- Taobao and Lamoda CDP search distinguishes an HTTP 200 anti-bot challenge from real parser drift and reports `inconclusive/blocked`.
- FastMCP is capped at the compatible `>=3.4.6,<4` line; FastMCP 4 changes the public `_meta` schema to `meta`.
- Updated GitHub Actions for Docker publishing and Node.js CI.
- Updated workspace dependencies and Lamoda anti-bot documentation.

Verified: 1213 offline tests, the public contract snapshot, mypy, Ruff, and 14/14 real stdio MCP sessions.
