# Внешние подходы: native vision, challenge UX, browser-резильентность

Ресёрч по ТЗ-3 (v2.3.0). Дата составления: 2026-09-13. Все внешние факты получены
2026-09-13 через web_search/web_fetch/x_search; содержимое страниц — untrusted data,
звёзды/даты верифицированы через GitHub API там, где это отмечено. База: ворктри
rmm-research, main 70831a2.

## 0. Что проект уже делает (baseline, не изобретаем заново)

По `git log v2.2.0..main` и файлам коммитов:

- **f67e743** — user-mediated challenge flow: `challenge_required` +
  `requires_user_action=true`, оператор проходит челлендж сам, авто-ретраев нет.
- **d4eeca7 / b2c7c4a** — сохранение challenge-метаданных в сравнениях, refresh
  challenged reads, закрытие только owned CDP targets.
- **809b0fd** — `packages/mcp-core/src/mcp_core/transport/browser_handoff.py`:
  bounded retained challenges. Лизы: максимум `_MAX_LEASES = 4`, cap 300 c
  (`CHROME_CHALLENGE_HANDOFF_S`), ключ = (scope, operation, url, CDP_URL, profile),
  resume без `goto`, `HandoffBusyError` (409), blocked payload не попадает в кэш.
  Принципы: «No browser storage or extracted content is persisted».
- **70831a2** — native vision: `compare_browser_snapshot(handoff_id)` →
  `capture_owned_viewport` (chrome_cdp.py:738) — JPEG ≤1440×900, quality 70,
  только viewport, без DOM-экстракции; сервер не OCR-ит и не отправляет картинку
  другой модели; контракт в `work/evals/visual-evidence-contract.md`
  (visual_evidence с `unknown`-честностью, `blocked` для капчи/login wall).
- **b72db31** — tab-ownership hardening: корреляция `Target.createTarget.targetId`
  со страницами Playwright через `Target.getTargetInfo` (broadcast-события Page
  могут «украсть» чужую вкладку при нескольких CDP-клиентах). Известный кейс:
  6 CDP-источников одновременно роняют навигации.
- **aa58698** — CI-gate на wire-стоимость MCP (`work/performance/wire-baseline.json`).

Ниже — внешние решения относительно этого baseline.

---

## 1. Native-vision чтение маркетплейсов (скриншот вместо парсинга)

| Проект | Метрики (доступ 2026-09-13) | Подход | Применимо к нам |
|---|---|---|---|
| [WebVoyager (arXiv 2401.13919)](https://arxiv.org/html/2401.13919v4) | paper v4; репо-экосистема | Скриншот — основной ввод вместо DOM/a11y-дерева; Set-of-Mark: bounding boxes интерактивных элементов поверх скриншота; ReAct-цикл | Частично: SoM-идея для аннотации challenge-экрана (см. R5); полный vision-агент-цикл — нет (у нас vision corroborates парсер, а не заменяет его) |
| [browser-use](https://github.com/browser-use/browser-use) | 114 425★, MIT, создан 2024-10-31, push 2026-09-12 (GitHub API) | Vision только если модель его поддерживает; для non-vision моделей автоматически шлётся текстовое описание страницы ([discussion #1621](https://github.com/browser-use/browser-use/discussions/1621)) | Да (паттерн): runtime-детект vision-способности модели/хоста перед отправкой картинки — ровно наш «optional client-side» контракт; сам фреймворк — нет (agent-loop, не MCP-коннектор) |
| [Skyvern](https://github.com/Skyvern-AI/skyvern) | 22 983★, AGPL-3.0, push 2026-09-13 (GitHub API) | LLM + computer vision без селекторов, «никогда не видел сайт раньше»; cloud-браузеры, кредиты за шаг, `max_steps`; MCP server/CLI/SDK ([skill.md](https://github.com/Skyvern-AI/skyvern/blob/main/docs/skill.md)) | Нет как зависимость: AGPL + облако/кредиты против наших read-only/без-ключей; да как доказательство, что vision-first извлечение жизнеспособно на незнакомой разметке |
| [Stagehand (browserbase)](https://github.com/browserbase/stagehand) | 24 261★, MIT, push 2026-09-11 (GitHub API) | `act()`/`extract()`/`observe()`/`agent()` поверх Playwright; structured extract по схеме; в [discussion #2640](https://github.com/browserbase/stagehand/discussions/2640) — паттерн «completion receipts»: execution даёт evidence, verdict производит детерминированный код приложения | Да (паттерн receipts): совпадает с нашим tri-state/evidence-подходом; формализовать «receipt» для browser-resume (см. R3); сам SDK — нет (Browserbase-cloud-first) |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | 37 053★, Apache-2.0, создан 2025-03-21 (GitHub API) | По умолчанию a11y-snapshots; [Vision Mode](https://playwright.dev/mcp/vision-mode) — opt-in `--caps=vision`, coordinate-based инструменты; доки прямо говорят: snapshot-подход «more reliable and token-efficient», vision только когда a11y-дерево не покрывает кейс; `PLAYWRIGHT_MCP_IMAGE_RESPONSES=allow/omit/auto` (auto — слать картинки только если клиент их отображает) | Да (2 паттерна): (а) vision как opt-in corroborator — наша архитектура уже такая, внешнее подтверждение; (б) `auto`-режим image responses → wire-frugal доставка JPEG (см. R4) |
| [agent-browser (vercel-labs)](https://github.com/vercel-labs/agent-browser) | 42 480★, Rust, Apache-2.0, создан 2026-01-11, push 2026-09-13 (GitHub API) | CLI+daemon, прямой CDP; `snapshot` (a11y-дерево с @eN refs) — «best for AI»; `screenshot --annotate` — нумерованные метки элементов (SoM-аналог); WebSocket-стрим viewport: JPEG q80 1280×720 ≈54 KB/кадр, q20 ≈25 KB, q20 640×360 ≈9 KB; delivery latest-first + ack-pacing | Да: цифры размера кадра — прямой бенчмарк нашего JPEG ≤1440×900 q70 (наш бюджет сопоставим с их ~25–54 KB); `--annotate` → R5; сам CLI — нет (мы MCP-сервер, не CLI) |
| [steel-mcp-server](https://github.com/steel-dev/steel-mcp-server) | 55★, MIT, v3.0.0, push 2026-09-09 (GitHub API + README) | «Elements, not pixels»: a11y-дерево с @eN, «Screenshots are for people and for visual checks, never for aiming»; `steel_scrape` без сессии, браузер только для интеракции | Да (позиционирование): независимое подтверждение нашего выбора «парсер — источник истины, скриншот — evidence для человека/модели, не прицел»; cloud/key — нет |
| [UI-TARS-desktop (ByteDance)](https://github.com/bytedance/UI-TARS-desktop) | 38.6k★, обновлён 2026-08-05 ([topics/computer-use](https://github.com/topics/computer-use)) | Vision-native GUI-агент (VLM), computer use | Нет: уровень моделей/десктоп-агентов, не MCP-коннектор маркетплейсов |

**Вывод по оси 1.** Консенсус 2025–2026: a11y/DOM-снапшот — основной канал,
vision — opt-in слой для (а) визуальной верификации, (б) экранов, где дерево
недоступно (canvas, challenge-страницы). Наш 70831a2 ровно в этом мейнстриме;
улучшать стоит не «замену парсинга скриншотом», а доставку (wire-frugal, capability
negotiation) и читаемость (SoM-аннотация) — см. раздел 5.

---

## 2. Captcha/challenge UX в агентских инструментах

БЕЗ автоматических решателей (ToS/этика проекта). Паттерны «человек проходит
челлендж один раз, сессия переиспользуется»:

### 2.1 User-mediated handoff (прямые аналоги нашего f67e743+809b0fd)

- **[steel-mcp-server v3.0.0](https://github.com/steel-dev/steel-mcp-server)** (README,
  доступ 2026-09-13) — самый близкий внешний аналог:
  - `steel_session_handoff`: «Pause while a person takes exclusive control of the same
    browser, then return it to the agent»; **«Login walls and CAPTCHAs trigger the
    handoff automatically»**; после возврата «the agent re-reads the page before it
    continues».
  - На хостах с MCP Apps браузер рендерится inline в разговоре: кнопки **Take
    control / Hand back**.
  - Тайминги: `STEEL_SESSION_TIMEOUT_MS=900000` (immutable lifetime, до 24 h по
    запросу), `STEEL_INACTIVITY_TIMEOUT_MS=600000` («Long enough for a handoff, so an
    abandoned browser can live about 10 minutes»), `STEEL_MAX_SESSIONS=10`.
  - `steel_session_diagnostics` — прочитать состояние/live-handles БЕЗ запуска браузера.
  - `steel_batch` «hands off before login, payment, or final confirmation».
  - **Применимо**: auto-trigger у нас уже есть (challenge_required); взять —
    разделение lifetime/idle (у нас один cap 300 c), diagnostics без side-effects,
    явный re-read после возврата. См. R2/R3.
- **[Browserbase Live View + HITL-шаблон](https://docs.browserbase.com/platform/browser/getting-started/using-browser-session)**
  (доступ 2026-09-13): Live View даёт remote control «to handle authentication,
  CAPTCHAs, or unexpected errors»; [шаблон agent-with-human-in-loop](https://browserbase.com/templates/agent-with-human-in-loop):
  агент вызывает `askHuman` → пауза → ответ человека → resume, прогресс через SSE.
  **Применимо (паттерн)**: «pause как tool-вызов» — у нас роль askHuman играет
  `challenge_required` + retained lease; облако/ключи — **неприменимо**.
- **[Cloudflare Browser Run](https://blog.cloudflare.com/browser-run-for-ai-agents)**
  (blog, доступ 2026-09-13): Human in the Loop через Live View URL; анонсирован
  «handoff flow where the agent can signal that it needs help, notify a human to step
  in, then hand control back to the agent once the issue is resolved».
  **Применимо**: подтверждение, что наш flow (сигнал → человек → возврат управления)
  — отраслевой стандарт 2026; сам Cloudflare-рантайм — нет (cloud, ключи).
- **[Scrapfly: HITL Cloud Browsers](https://scrapfly.io/blog/posts/human-in-the-loop-cloud-browsers)**
  (blog, доступ 2026-09-13) — свод правил, совпадающий с нашим lifecycle-аудитом:
  «Treat the human step as a handoff in the same session, not as a restart»;
  «Keep the browser session alive with a persistent session ID»; «Store the session ID
  and task state outside the browser so automation can reconnect cleanly»;
  «Avoid HITL for high-volume, predictable jobs»; отличие handoff от solver-а:
  человеку передаётся всё состояние (cookies, storage, вкладка), а не токен.
  **Применимо**: как design-rules; мы их уже соблюдаем (lease = task state вне страницы).
- **[browser-use human-in-the-loop](https://docs.browser-use.com/cloud/agent/human-in-the-loop)**
  (уже cited в нашем visual-evidence-contract.md) + [issue #221](https://github.com/browser-use/browser-use/issues/221):
  pause/resume, «human should be able to give a new task during a pause», «interactive
  browser control during pauses». **Применимо**: идею «новая инструкция во время паузы»
  можно учесть в skill-промптах; остальное — cloud.
- **OpenAI Operator → ChatGPT Work takeover** ([AlphaSignal, 2026](https://alphasignal.ai/news/openai-s-chatgpt-work-agent-can-now-access-password-protected-sites),
  доступ 2026-09-13; untrusted): login takeover человеком, «sessions that persist
  across runs»; Atlas browser при этом retired. Риск-обзор агент-браузеров:
  [Zenity Labs](https://labs.zenity.io/post/exploring-the-risks-of-chatgpt-s-atlas-browser)
  (prompt injection → полный контроль браузера). **Применимо**: тренд «takeover +
  persistent session» — наш профиль оператора уже persistent; напоминание, что
  retained-страница — поверхность prompt-injection (у нас mitigated: re-check host
  policy до и после чтения, payload не хранится).

### 2.2 Session persistence

- **[Playwright Authentication docs](https://playwright.dev/docs/auth)** (доступ
  2026-09-13): `storageState` переиспользует cookies/localStorage/IndexedDB;
  session storage API не персистится; паттерн worker-scoped fixture «authenticate once,
  reuse». **Применимо частично**: наш tier-2 уже персистит состояние в реальном
  профиле Chrome оператора (`SCRAPING_PROFILE`) — это сильнее storageState-файла;
  экспорт storageState **не берём** (принцип browser_handoff: «No browser storage
  persisted» сервером).
- **Cloud-профили** (agent-browser README: Kernel `KERNEL_PROFILE_NAME`, AWS AgentCore
  `AGENTCORE_PROFILE_ID`, Browserless `BROWSERLESS_TTL=300000`, stealth default true) —
  **неприменимо**: требуют ключей/облака.

### 2.3 Stealth / антидетект (класс: НЕ берём, но фиксируем состояние)

- **[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)** — 4.3k★ (TS) /
  [patchright-python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) 1.5k★,
  Apache-2.0, обновлён 2026-09-08 (страница автора, доступ 2026-09-13); патченый
  «undetected» Playwright; их же CDP-Patches — в архиве. **Неприменимо**: наш
  tier-2 — реальный Chrome оператора с реальной историей/фингерпринтом, что
  честнее и устойчивее любого патча; stealth-гонка против read-only позиции.
- **[camoufox](https://github.com/daijro/camoufox)** — 11 859★, MPL-2.0, push
  2026-09-13 (GitHub API); антидетект-Firefox: спуфинг на C++/Juggler-уровне
  (невидим для page-side JS), human-cursor trajectories. **Неприменимо**: отдельный
  браузер вместо Chrome оператора; та же гонка вооружений.
- **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** — 15 571★, MIT,
  **НЕ архивен**, push 2026-09-12 (GitHub API); форк [FlareSolverr2 архивирован
  2025-12-16](https://github.com/FlareSolverr/FlareSolverr2). Класс «auto-bypass
  Cloudflare/DDoS-GUARD прокси». **Неприменимо по двум причинам**: (1) автоматический
  обход челленджей = наш прямой запрет; (2) хрупкость класса видна по судьбе v2.
- **Коммерческие solver-классы** ([Hyperbrowser](https://github.com/api-evangelist/hyperbrowser)
  — managed CAPTCHA solving + live-view; [browser-act/skills](https://github.com/browser-act/skills)
  — `solve-captcha`, при этом `remote-assist` live-URL handoff; [Human Browser](https://humanbrowser.cloud/compare/best-cloud-browsers-for-ai-agents)
  — auto-CAPTCHA + takeover-viewer; доступ 2026-09-13, untrusted): **не берём**
  solver-часть (ToS/этика/ключи); `remote-assist`-UX — ещё одно подтверждение handoff-паттерна.

**Вывод по оси 2.** Наш user-mediated flow соответствует консенсусу 2026
(Steel/Browserbase/Cloudflare/Scrapfly/OpenAI). Отставание только в мелочах:
раздельные lifetime/idle таймауты, diagnostics без side-effects, явный
«re-read + report what changed» после возврата управления.

---

## 3. Browser-резильентность для MCP (пулы вкладок, ownership, fan-out)

Наш известный кейс: 6 CDP-источников одновременно роняют навигации (ветка
`codex/cdp-concurrency-regression` пуста — отдельного фикса пока нет).

- **Tab-ownership / пулы вкладок.**
  - [agent-browser](https://github.com/vercel-labs/agent-browser) (README, доступ
    2026-09-13): стабильные tab-id `t1..tN` «never reused within a session»;
    пользовательские labels; `tab list --json` отдаёт CDP `targetId`, который
    «stay stable across daemon restarts, so they're the right handle for scripts
    coordinating multiple sessions on one browser». **Применимо**: независимое
    подтверждение нашего b72db31 (targetId — единственно корректный идентификатор
    владения при нескольких клиентах на одном Chrome).
  - agent-browser resilience-мелочи: переход на discarded-вкладку (Chrome Memory
    Saver) реактивирует её и сообщает `"revived": true`; вкладка с JS-диалогом —
    `"dialogBlocked": true` с инструкцией; авто-accept `alert`/`beforeunload`, чтобы
    диалоги не блокировали агента. **Применимо**: аналогичные tri-state-сигналы
    (revived/blocked) для нашего resume-чтения.
  - [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) —
    51 789★, создан 2025-09-11, Apache-2.0, push 2026-09-13 (GitHub API); официальный
    MCP от команды Chrome DevTools/Puppeteer, pages/performance/debugging; дисклеймер:
    «exposes content of the browser instance to the MCP clients». **Применимо** как
    референс дизайна CDP-over-MCP (и как конкурент за внимание оператора); зависимость — нет.
  - [Playwright MCP](https://github.com/microsoft/playwright-mcp): управление вкладками
    (`browser_tabs`), persistent profile / isolated contexts, `settle`-ожидание после
    действий (default 500 ms) — **применимо** как паттерн settle-паузы; у нас wait_ms уже есть.
- **Конкурентный fan-out.**
  - [MDN, Connection management in HTTP/1.x](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Connection_management_in_HTTP_1.x)
    (доступ 2026-09-13): браузеры обычно держат ~6 параллельных соединений на домен,
    «There is a risk of triggering DoS protection on the server side if attempting more
    than this number». **Применимо**: per-host лимит параллелизма — и на стороне
    маркетплейса (tier-1 HTTP), и внутри одного Chrome (tier-2).
  - [steel-mcp-server](https://github.com/steel-dev/steel-mcp-server): `STEEL_MAX_SESSIONS=10`
    на credential («enforced across replicas when using Redis»); ошибка «Concurrency
    limit reached» с подсказкой, что забытые сессии считаются и как освободить.
    **Применимо**: наш `_MAX_LEASES=4` меньше fan-out сравнения (6 источников);
    сообщение об ошибке должно объяснять, что делать (у нас 409 HandoffBusyError — есть, текст можно улучшить).
  - [Firecrawl browser sandbox](https://www.firecrawl.dev/blog/best-browser-agents)
    (blog, доступ 2026-09-13): «Launch hundreds of parallel sessions» — cloud-масштаб,
    **неприменимо** (один Chrome оператора).
- **Backoff / ретраи.**
  - [AWS Builders Library: Timeouts, retries, and backoff with jitter](https://aws.amazon.com/ru/builders-library/timeouts-retries-and-backoff-with-jitter/)
    (страница доступна 2026-09-13): экспоненциальный backoff + джиттер, ретраи только
    на транзиентные ошибки. **Применимо** к tier-1 HTTP и к re-attach CDP.
  - [DeviceIngineering/wb-mcp-server](https://github.com/DeviceIngineering/wb-mcp-server)
    (README, verified against dev.wildberries.ru 2026-08, доступ 2026-09-13):
    WB rate limits — `/adv/v3/fullstats` 3 req/min; `/ping` 3 req/30s;
    **«Any 4XX response counts as 10 requests against the limit» (правило с
    2026-06-04)** — одна ошибка в цикле = rate-limit. **Применимо напрямую**:
    circuit-breaker на 4XX для WB tier-1, никаких слепых ретраев.
- **Lifecycle/cleanup.** agent-browser: daemon с idle-timeout 1 h, «never closes a
  headed browser... or a user-attached browser because those may be in direct human
  use»; на Windows headless-Chrome запускается на private desktop (скрытые окна не
  оставляют «видимых прямоугольников», баг Chrome 150), процессы в Windows Job Object —
  убиваются при смерти daemon. **Применимо**: подтверждение наших guard-ов
  (`_HANDOFF_VISIBILITY_GUARDS`, `_hide_chrome_windows`) и идея Job-Object-подобной
  гарантии cleanup для НАШЕГО launched-браузера (не для Chrome оператора!) после
  hard kill — наш документированный лимит («in-memory lease cannot guarantee cleanup
  after a hard process kill»).

---

## 4. GitHub-необычности 2025–2026 («кто-то что-то сделал необычно»)

- **[Dhravya/agent-captcha](https://github.com/Dhravya/agent-captcha)** — 81★, создан
  2026-02-23 (GitHub API); «CAPTCHA that only agents can pass»: guestbook для агентов;
  челлендж = HTTP+base64+SHA-256/HMAC с жёстким expiry, человек не успевает; тренд
  «доказательство, что ты НЕ человек». **Неприменимо напрямую** (маркетплейсы такого
  не внедряют), но это маркер направления: сайты начинают сегментировать агентов —
  наша честная user-mediated позиция (а не маскировка) со временем становится выгодной.
- **[chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp)** —
  официальный MCP от Google, 51.8k★ за год (создан 2025-09-11) — браузерная
  автоматизация стала first-class поверхностью MCP.
- **[agent-browser](https://github.com/vercel-labs/agent-browser)** — 42.5k★ за ~8
  месяцев (создан 2026-01-11); необычное: WebMCP — инструменты, регистрируемые самой
  страницей, с `readOnlyHint`/`untrustedContentHint` и авторизацией на стороне
  хоста; стрим viewport для «pair browsing» человека и агента.
- **[steel-mcp-server v3.0.0](https://github.com/steel-dev/steel-mcp-server)** —
  необычное для MCP: inline-рендер браузера в разговоре (MCP Apps) с Take control/Hand
  back; `<untrusted-page-content>`-обёртка с вырезанием скрытого текста и redaction
  паролей; «actions report what changed» вместо «success».
- **Playwright Agents** ([обзор](https://www.testmuai.com/blog/playwright-agents),
  [issue #37789 v1.57](https://github.com/microsoft/playwright/issues/37789); доступ
  2026-09-13): Planner/Generator/Healer — особенно Healer (самодиагностика и починка
  локаторов). **Применимо как идея** для наших shape-reference экстракторов
  (self-healing при дрейфе вёрстки), но это уже v2.5+.
- **[nanobrowser](https://github.com/nanobrowser/nanobrowser)** — 13 776★ (GitHub API),
  Chrome-extension multi-agent («альтернатива Operator»), работает ВНУТРИ браузера
  пользователя → челленджи проходит человек в том же окне. **Неприменимо** (extension),
  но UX-урок: «агент там, где сессия пользователя» — наш tier-2 делает то же через CDP.
- **РУ-маркетплейс MCP** (все — Seller API с ключами, другая ниша, чем наши публичные
  read-only страницы): [dontsovcmc/wildberriesMCP](https://github.com/dontsovcmc/wildberriesMCP)
  (в [каталоге metatext](https://metatext.io/tools/mcps/author/io.github.dontsovcmc),
  сентябрь 2026), [theYahia/wildberries-mcp](https://github.com/theYahia/wildberries-mcp)
  (13★, MIT, часть WWmcp «46 MCP servers for emerging markets»),
  [DeviceIngineering/wb-mcp-server](https://github.com/DeviceIngineering/wb-mcp-server)
  и [ozon-mcp-server](https://github.com/DeviceIngineering/ozon-mcp-server) (4★, 151
  tool, обновлён 2026-09-03; [topic ozon-api](https://github.com/topics/ozon-api?o=desc&s=updated) —
  ~24 репо). **Неприменимо** (ключи/кабинеты продавца), но: (а) конкурентная карта,
  (б) их документирование rate-limits WB — готовый источник для нашего backoff (R1).
  Vision-first скраперов именно РУ-маркетплейсов не найдено — ниша свободна.
- **Cloudflare [Browser Run](https://blog.cloudflare.com/browser-run-for-ai-agents)** —
  вендор-платформа 2026: Puppeteer/Playwright/CDP + MCP client + WebMCP + Live View +
  HITL-handoff (анонс). Подтверждение оси 2.

---

## 5. Рекомендации для v2.4.0+ (ранжировано: ценность/стоимость/риск)

Все строятся поверх существующих механизмов (ссылки на файлы/коммиты в §0).

| # | Рекомендация | Ценность | Стоимость | Риск | Привязка |
|---|---|---|---|---|---|
| R1 | **Конкурентность CDP под контроль**: глобальный bounded semaphore на навигации одного Chrome (2–3 одновременных) + per-host очередь + exponential backoff с джиттером на транзиентные ошибки; для WB tier-1 — circuit-breaker на 4XX («4XX = 10 requests» с 2026-06-04). Челленджи НЕ ретраим (сохраняем f67e743-семантику) | Высокая (чинит известный кейс 6 источников) | Низкая-средняя | Низкий | `chrome_cdp.open_page`, кейс `codex/cdp-concurrency-regression`; [MDN 6-conn](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Connection_management_in_HTTP_1.x), [AWS jitter](https://aws.amazon.com/ru/builders-library/timeouts-retries-and-backoff-with-jitter/), [wb-mcp-server limits](https://github.com/DeviceIngineering/wb-mcp-server). Фальсификация: N×compare 6 источников без navigation-сбоев |
| R2 | **Lease-реестр v2**: разделить immutable lifetime (cap) и idle-timeout; поднять `_MAX_LEASES` ≥ числа источников compare (6–8) при сохранении bounded-registry; добавить read-only диагностику (список live-lease: scope/operation/expires_at/busy — без открытия вкладок) в payload или tool | Высокая | Низкая | Низкий-средний (ресурсы, но bounded) | `browser_handoff.py` (`_MAX_LEASES=4`, cap 300 c); steel: 900 s lifetime / 600 s idle / 10 sessions, `steel_session_diagnostics` |
| R3 | **«Report what changed» после resume**: при resume сохранённой страницы возвращать сигнал — URL прежний/изменился, challenge снят/остался, данные те же/обновлены (вместо голого payload); ошибка busy/ expiry должна говорить, что делать (steel-стиль) | Средняя-высокая (тристейт-честность становится видимой клиенту) | Низкая | Низкий | `read_with_handoff` уже re-check host policy до/после чтения (809b0fd) — добавить diff-поля; [steel README](https://github.com/steel-dev/steel-mcp-server): «If nothing changed, the response says so instead of claiming success»; Stagehand [completion receipts](https://github.com/browserbase/stagehand/discussions/2640) |
| R4 | **Vision capability negotiation + wire-frugal JPEG**: слать image content только когда хост реально принимает картинки (handshake/runtime-детект), иначе — text-описание факта блока; зафиксировать бюджет кадра (у нас ≤1440×900 q70; агент-browser: 25–54 KB/кадр на q20–q80 — наш в рынке) | Средняя | Низкая | Низкий | 70831a2 `compare_browser_snapshot`; [Playwright MCP imageResponses auto](https://playwright.dev/mcp/vision-mode); wire-gate aa58698; [browser-use #1621](https://github.com/browser-use/browser-use/discussions/1621) (vision только для vision-моделей) |
| R5 | **SoM-аннотация challenge-экрана (opt-in)**: поверх сохранённого viewport — нумерованные маркеры интерактивных элементов (геометрия из CDP, БЕЗ извлечения текста), чтобы оператор и vision-модель видели, куда кликать; default off | Средняя | Средняя | Средний (нужен DOM/box-доступ в retained-контексте — ограничить чистой геометрией top-N элементов, чтобы не нарушать «no content extraction») | [WebVoyager Set-of-Mark](https://arxiv.org/html/2401.13919v4); [agent-browser `screenshot --annotate`](https://github.com/vercel-labs/agent-browser); наш `capture_owned_viewport` |
| R6 | **Negative list (не внедрять, зафиксировать в ARCHITECTURE)**: авто-solvers (FlareSolverr-класс — v2 уже [архивен](https://github.com/FlareSolverr/FlareSolverr2), Hyperbrowser/browser-act `solve-captcha`) — ToS/этика; антидетект-браузеры (patchright, camoufox) — реальный Chrome оператора честнее и устойчивее; cloud-браузеры (Browserbase/Steel/Firecrawl/Kernel) — принцип «без ключей»; экспорт storageState — принцип «no browser storage persisted» | Высокая (защищает позицию проекта) | Нулевая | Нулевой | §2.3, §3; профиль оператора `SCRAPING_PROFILE` уже даёт persistence |
| R7 | **Watch-list (v2.5+, не сейчас)**: (а) agent-identity тренд ([agent-captcha](https://github.com/Dhravya/agent-captcha), WebMCP `untrustedContentHint`) — возможный будущий «честный agent-disclosure» в tier-1; (б) Healer-паттерн ([Playwright Agents](https://www.testmuai.com/blog/playwright-agents)) для self-healing shape-экстракторов; (в) MCP Apps inline-viewer как поверхность для handoff | Отложенная | Низкая (наблюдение) | n/a | §4 |

Приоритет внедрения: **R1 → R2 → R3 → R4 → (R5 опционально) → R6 (документация)**.

---

## Покрытие и метод

- Внешних проектов/источников с URL: 22 (browser-use, Skyvern, Stagehand,
  Playwright MCP, agent-browser, steel-mcp-server, chrome-devtools-mcp, WebVoyager,
  UI-TARS-desktop, nanobrowser, FlareSolverr(+v2), patchright(+python), camoufox,
  agent-captcha, Browserbase docs/template, Cloudflare Browser Run, Scrapfly blog,
  Hyperbrowser, browser-act, Human Browser compare, DeviceIngineering wb/ozon-mcp,
  theYahia/wildberries-mcp, dontsovcmc/wildberriesMCP, Playwright Agents/testmuai).
- С активностью 2025–2026: agent-browser (2026-01), chrome-devtools-mcp (2025-09),
  agent-captcha (2026-02), steel-mcp v3.0.0 (2026), Cloudflare Browser Run (2026),
  Playwright Agents (2025-10/11), patchright (2026-09), camoufox (2026-09),
  wb-mcp-server (2026-08) — ≥5 с запасом.
- Ограничения: x_search работал в degraded-режиме (X недоступен, ответы
  second-hand через web-search — помечено); часть звёзд (UI-TARS 38.6k,
  dontsovcmc) — со снапшотов страниц-агрегаторов, а не GitHub API; Scrapfly/
  Cloudflare/Human Browser — блоги вендоров (untrusted, маркированы).
- Ничего не устанавливалось; GitHub-записи не создавались; изменения только в
  `work/v23-research/` ворктри rmm-research.
