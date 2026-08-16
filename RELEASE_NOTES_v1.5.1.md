# RELEASE NOTES — v1.5.1 (2026-08-16)

Патч для официального MCP-реестра: v1.5.1 публикует OCI-образ, который клиент
может запустить как stdio-сервер через `docker run --rm -i`, и добавляет
автоматическую публикацию в реестр на каждый тег. Основной функциональный
релиз — v1.5.0; здесь меняется только канал доставки.

## Гейт выпуска

| Проверка | Результат |
|---|---|
| `ruff check` + `ruff format --check` | зелёный |
| `mypy` (host / win32 / darwin) | 87 файлов, 0 ошибок |
| `pytest -m "not live and not cdp"` | 1181 passed, 1 skipped (1182) |
| `check_versions.py 1.5.1` | 72 места согласованы |
| `check_test_count.py` / `check_no_print.py` / `uv lock --check` | зелёные |
| `e2e_stdio_check.py` | 13/13 локальных серверов |
| Сборка и публикация OCI + реестр | автоматически на теге `v1.5.1` (workflow `mcp-registry-publish`) |

## Что вошло в патч

### Добавлено

- **`Dockerfile.stdio`** — вариант образа для MCP-клиентов: `MCP_TRANSPORT=stdio`,
  `CMD ["marketplace-mcp"]` (34 инструмента), метка
  `io.modelcontextprotocol.server.name` для верификации владения в реестре.
- **`scripts/e2e_stdio_check_docker.py`** — настоящая сессия MCP через
  `docker run --rm -i`: initialize → tools/list (ровно 34) → вызов
  `marketplace_sources` (12 смонтированных источников). Это закрывает
  прежнее «Docker не проверен» из v1.5.0 по крайней мере для stdio-пути.
- **`.github/workflows/mcp-registry-publish.yml`** — на тег `v*` собирает и
  публикует образ в GHCR, прогоняет docker-stdio probe, затем
  `mcp-publisher login github-oidc` и `mcp-publisher publish` для официального
  MCP-реестра.
- **`server.json`** — добавлен OCI-пакет
  `ghcr.io/vladimir-human/ru-marketplace-mcp:1.5.1` с `runtimeHint: docker`;
  файл проходит JSON Schema `2025-12-11` без ошибок.

### Изменено

- Версия всех 72 объявлений поднята на 1.5.1, включая пакеты workspace,
  `uv.lock`, dsh-бандл и Docker-теги.

## Проверка публикации

Факт успешной публикации в официальный MCP-реестр фиксируется ран-логом
workflow `mcp-registry-publish` для тега `v1.5.1`, а не текстом этого файла.
