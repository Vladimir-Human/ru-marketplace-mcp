# Contributors / Участники

This project keeps a durable thank-you record for people whose merged pull
requests improved it. The PR links are the source of detail; this file is a
human-readable index, so future maintainers do not have to reconstruct credit
from chat history.

Проект сохраняет здесь список авторов влитых pull request. Ссылки на PR —
первоисточник деталей; этот файл нужен, чтобы благодарности не терялись в
истории обсуждений.

## People / Люди

| Contributor | Merged PRs | Contribution |
|---|---|---|
| [@Vladimir-Human](https://github.com/Vladimir-Human) | maintainer | Project architecture, releases, reviews, and core maintenance. |
| [@Xpos587](https://github.com/Xpos587) | [#5](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/5) | MPStats connector: plugin API analysis, parser structure, and the first working version. |
| [@avxone](https://github.com/avxone) | [#37](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/37) | Avito selfcheck fix for the `catalog.items[]` response envelope. |
| [@Khalmatov](https://github.com/Khalmatov) | [#38](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/38) | Ozon review provenance: mark the SKU the review is about. |
| [@fosteev](https://github.com/fosteev) | [#42](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/42), [#47](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/47), [#106](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/106) | macOS CDP stealth portability, the Cian real-estate connector, and Cian regional redirects with card photo URLs. |
| [@ilodezis](https://github.com/ilodezis) | [#48](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/48), [#132](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/132) | Selective unified-server mounting through `MARKETPLACE_SOURCES`, reducing tool-schema wire cost; the security refresh that cleared every published advisory from the lockfiles and pinned workflow actions and Docker base images. |
| [@NektoDron](https://github.com/NektoDron) | [#94](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/94) | Wildberries: reading the gated wb.ru API hosts through curl_cffi browser impersonation. |
| [@Pegas-us](https://github.com/Pegas-us) | [#107](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/107), [#108](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/108), [#192](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/192), [#193](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/193), [#194](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/194), [#195](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/195), [#198](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/198), [#201](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/201), [#202](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/202), [#204](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/204), [#205](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/205), [#206](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/206) | Citilink search parameter fix; the opt-in `WB_TRANSPORT=dom` Wildberries transport; raw-CDP initial `about:blank` handling and owned-popup `aliexpress_card` reads; `ozon_reviews` `usefulness_desc` pagination; and Avito reservation status, extended deal/seller/location card fields, sale prices (`old_price_rub` / `discount_percent`), opt-in gallery photos (`#199`), and service-state closure handling, plus bug reports [#196](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/196) and [#197](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/197). |
| [@dont99code](https://github.com/dont99code) | [#133](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/133) | Ozon: a 403 that the site's own JS check clears on reload is no longer reported as a block, which ended intermittent `ozon_search` failures. |

## Reports / Репорты

| Reporter | Issue | Contribution |
|---|---|---|
| [@HeckfyCU](https://github.com/HeckfyCU) | [#190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190) | Timed every AliExpress navigation request from a Russian residential IP and pinpointed the page-readiness deadline that kept `aliexpress_search` from returning offers. |
| [@XaruStar](https://github.com/XaruStar) | [#214](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/214) | Captured live Ozon `webListReviews` payloads returning decimal-string `itemId` values and traced why `_parse_review_item`, `ozon_reviews` (`own_reviews`), and `ozon_selfcheck` silently dropped review SKU attribution. |

## Automation / Автоматизация

| Contributor | Merged PRs | Contribution |
|---|---|---|
| [dependabot[bot]](https://github.com/apps/dependabot) | [#2](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/2), [#3](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/3), [#4](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/4), [#17](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/17), [#23](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/23), [#24](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/24), [#41](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/41), [#114](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/114), [#124](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/124), [#135](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/135), [#136](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/136), [#182](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/182), [#188](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/188) | Dependency updates and supply-chain maintenance. |

If a merged contribution is missing or the description needs correction, please
open an issue or pull request so the record stays accurate.

Если вклад пропущен или описание нужно исправить, откройте issue или pull request,
чтобы список оставался точным.
