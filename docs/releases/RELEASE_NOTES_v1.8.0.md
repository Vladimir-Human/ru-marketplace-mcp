# v1.8.0

## Русский

Добавлен практический фильтр доступности в `compare_prices`:

- `in_stock_only=true` выбирает победителя и считает spread только среди предложений, где источник явно сообщил наличие;
- исходный список не скрывает исключённые предложения;
- предупреждения показывают, сколько цен исключено из ranking из-за отсутствия подтверждённого stock.

Проверено: compare tests, offline suite, public contract, mypy, Ruff и GitHub CI.

## English

`compare_prices` now supports an availability-aware purchase flow:

- `in_stock_only=true` selects the winner and computes the spread only from offers whose source explicitly reports stock;
- the raw offer list remains visible for auditability;
- warnings report how many priced offers were excluded because stock was not confirmed.

Verified: comparison tests, offline suite, public contract, mypy, Ruff, and GitHub CI.
