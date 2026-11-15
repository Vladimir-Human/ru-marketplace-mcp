# v2.9.3 — 2026-11-15

Патч-релиз. DNS снова отвечает через Chrome со scraping-профилем, чтение Wildberries через Chrome больше не обрывается на самопереходе страницы, а для Ozon появилась необязательная настройка для Chrome на серверах.

## Исправления

- **DNS и проверка Qrator** (#232). Поиск и карточки DNS больше не падают сразу на проверке Qrator. Она отвечает HTTP 401 и через пару секунд сама перезагружает страницу. Теперь на это даётся до 10 с, как на 403-проверку Ozon. Открыть сайт вручную нужно, только если проверка так и не прошла.
- **Wildberries через Chrome** (#230). Чтение страницы больше не падает с ошибкой `Execution context was destroyed`, когда сайт сам переходит на другую страницу во время проверки браузера. В этом случае страница открывается ещё один раз. Отказы, капчи и таймауты по-прежнему не повторяются.

## Новое

- **`OZON_CHROME_COMPAT`** (#230). Настройка принимает `native` (по умолчанию) или `windows`. В режиме `windows` собственные вкладки коннектора показывают Ozon систему Windows в User-Agent, `navigator.platform` и UA Client Hints. Версия Chrome и язык остаются настоящими. Другие площадки и открытые вручную вкладки не меняются. Режим может помочь, если Ozon сразу отвечает 403 Chrome на Linux-сервере. Капчу он не решает. В `docs/CDP_SETUP.md` описан запуск Chrome на сервере без монитора (Xvfb) и просмотр его окна через SSH.

## Как обновиться

- Без новой переменной всё работает как раньше.
- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.
- Через `uvx`: `uvx ru-marketplace-mcp@2.9.3 install <client> --write --force`.
- Включить режим Windows для Ozon: `OZON_CHROME_COMPAT=windows` в окружении сервера, затем перезапуск. Отключить: `native` или удалить переменную.

## Благодарности

Спасибо [@Donkiton](https://github.com/Donkiton) за #230 и [@ilodezis](https://github.com/ilodezis) за отчёт #232 с таблицей по площадкам.

## Состав

16 wheel и 16 sdist на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.3` и запись в MCP Registry.

---

## English

Patch release. DNS answers again through the scraping Chrome, Wildberries pages read through Chrome no longer break when the page navigates by itself, and Ozon gets an optional setting for Chrome on servers.

### Fixes

- **DNS and the Qrator check** (#232). DNS search and cards no longer fail at once on the Qrator check. It answers HTTP 401 and reloads the page by itself within a few seconds. That reload now gets up to 10 s, like Ozon's 403 check. A manual visit is needed only when the check does not clear.
- **Wildberries through Chrome** (#230). Reading a page no longer fails with `Execution context was destroyed` when the site navigates by itself during its browser check. The page is then opened once more. Refusals, captchas and timeouts are still not retried.

### New

- **`OZON_CHROME_COMPAT`** (#230). The setting takes `native` (the default) or `windows`. With `windows` the connector's own tabs present Windows to Ozon in the User-Agent, `navigator.platform` and UA Client Hints. The real Chrome version and language stay. Other sources and manually opened tabs are unchanged. It can help when Ozon answers 403 at once to Chrome on a Linux server. It does not solve captchas. `docs/CDP_SETUP.md` covers running Chrome on a server without a monitor (Xvfb) and viewing its window over SSH.

### Upgrading

- Without the new variable nothing changes.
- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.
- `uvx`: `uvx ru-marketplace-mcp@2.9.3 install <client> --write --force`.
- To turn on the Windows mode for Ozon: `OZON_CHROME_COMPAT=windows` in the server's environment, then restart. To turn it off: `native` or remove the variable.

### Thanks

Thanks to [@Donkiton](https://github.com/Donkiton) for #230 and to [@ilodezis](https://github.com/ilodezis) for report #232 with its per-source table.

### Contents

16 wheels and 16 sdists on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.3`, and the MCP Registry entry.
