"""Wildberries CLI-only transport/parser selfchecks."""
# START_MODULE_CONTRACT
# PURPOSE: Probe configured Wildberries transport/parser health for operators.
# SCOPE: CLI-only card, reviews, search_goods, search_v9 and root_basket probes; no MCP registration.
# DEPENDS: server (helpers, constants, models and transport); typing.
# LINKS: M-WB; R-WB-SELFCHECK-DOM; packages/wb-connector/tests.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _probe_card: Probe the API or rendered baseline card and return its nm id.
# _probe_reviews: Probe the feedbacks pool by the baseline imt id.
# _probe_search_goods: Probe the legacy id list and return its query.
# _probe_search_v9: Probe the configured API or rendered primary search path.
# _probe_root_basket: Probe baseline imt id resolution through basket CDN.
# run: Probe configured transport/parser health for operators; not an MCP tool.
# END_MODULE_MAP

from __future__ import annotations

from typing import Any

from . import server as S


# START_BLOCK_SELFCHECK
async def _probe_card(checks: dict[str, dict], use_dom: bool) -> int:
    # --- card (v4 API or rendered page) ---
    nm = S._SELFCHECK_NM
    if use_dom:
        checks["card"] = await S._wb_dom_selfcheck("card")
    else:
        card_url = (
            f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest={S.WB_DEFAULT_DEST}&locale=ru&spp=30&nm={nm}"
        )
        await S._polite_wait()
        try:
            async with S.asyncio.timeout(45):  # whole-subcheck wall-clock (audit CRASH_HANG)
                async with S._wb_client() as client:
                    status_code, text, err = await S._fresh_get_text(client, card_url)
                if err or status_code != 200:
                    checks["card"] = S.R.selfcheck_entry(
                        "inconclusive",
                        baseline=str(nm),
                        reason="transport_down",
                        notes=[f"http {status_code} err={err}"],
                    )
                else:
                    try:
                        data = S.json.loads(text or "")
                    except S.json.JSONDecodeError as exc:
                        checks["card"] = S.R.selfcheck_entry(
                            "drift",
                            baseline=str(nm),
                            reason="parse_error",
                            notes=[f"card 200 JSON parse error: {str(exc)[:120]}"],
                        )
                        data = None
                    if not isinstance(data, dict):
                        if data is not None:
                            checks["card"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                reason="schema_drift",
                                notes=[f"card 200 root is {type(data).__name__}, not object"],
                            )
                        elif "card" not in checks:
                            checks["card"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                reason="schema_drift",
                                notes=["card 200 root is null, not object"],
                            )
                        products: list[Any] = []
                    else:
                        products, products_error = S._card_products_checked(data)
                        if products_error:
                            checks["card"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                reason="schema_drift",
                                notes=[f"card 200 products container error: {products_error}"],
                            )
                    if not isinstance(data, dict) or ("card" in checks and checks["card"].get("state") == "drift"):
                        pass
                    elif not products:
                        # A SINGLE-SKU baseline returning 0 products is indistinguishable
                        # from deletion/OOS at this endpoint — baseline rot, NOT drift
                        # (audit 2026-06-01 FALSE_POSITIVE: don't cry wolf on a gone SKU).
                        checks["card"] = S.R.selfcheck_entry(
                            "inconclusive",
                            baseline=str(nm),
                            reason="baseline_gone",
                            notes=[
                                "no products on 200 body (baseline SKU deleted/OOS, "
                                "or schema drift — cannot distinguish on one SKU)"
                            ],
                        )
                    else:
                        p = products[0]
                        if not isinstance(p, dict):
                            checks["card"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                reason="schema_drift",
                                notes=[f"card product entry is {type(p).__name__}, not object"],
                            )
                        else:
                            missing = [
                                k
                                for k in ("id", "name", "sizes", "reviewRating", "feedbacks", "totalQuantity")
                                if k not in p
                            ]
                            cur, _orig = S._extract_price_rub(p)
                            qty = S.R.coerce_int(p.get("totalQuantity"))
                            if missing:
                                checks["card"] = S.R.selfcheck_entry(
                                    "drift",
                                    baseline=str(nm),
                                    missing_fields=missing,
                                    notes=[f"card missing critical fields: {missing}"],
                                )
                            elif qty is None:
                                checks["card"] = S.R.selfcheck_entry(
                                    "drift",
                                    baseline=str(nm),
                                    reason="quantity_shape_drift",
                                    notes=["card totalQuantity is present but not parseable as an integer"],
                                )
                            elif cur is None and qty and qty > 0:
                                checks["card"] = S.R.selfcheck_entry(
                                    "drift",
                                    baseline=str(nm),
                                    reason="price_shape_drift",
                                    notes=["in-stock baseline has positive totalQuantity but no parseable price"],
                                )
                            elif cur is None:
                                # price None is legit if the baseline SKU went OOS — rot, not drift.
                                checks["card"] = S.R.selfcheck_entry(
                                    "inconclusive",
                                    baseline=str(nm),
                                    reason="baseline_gone",
                                    notes=["price None (baseline SKU may be out of stock)"],
                                )
                            else:
                                checks["card"] = S.R.selfcheck_entry("healthy", baseline=str(nm), price=cur)
        except (TimeoutError, Exception) as exc:
            checks["card"] = S.R.selfcheck_entry(
                "inconclusive",
                baseline=str(nm),
                reason="timeout" if isinstance(exc, S.asyncio.TimeoutError) else "transport_down",
                notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
            )
    return nm


async def _probe_reviews(checks: dict[str, dict]) -> None:
    # --- reviews (feedbacks pool by imt_id) ---
    await S._polite_wait()
    try:
        async with S.asyncio.timeout(45):
            attempts: list[dict[str, Any]] = []
            review_payload: Any = None
            used_host = ""
            async with S._wb_client() as client:
                for host in S.WB_REVIEW_HOSTS:
                    status_code, text, err = await S._fresh_get_text(
                        client, f"https://{host}/feedbacks/v2/{S._SELFCHECK_IMT}"
                    )
                    if err or status_code != 200:
                        attempts.append(
                            {
                                "host": host,
                                "status": status_code,
                                "error": err or f"HTTP {status_code}",
                            }
                        )
                        continue
                    used_host = host
                    try:
                        review_payload = S.json.loads(text or "")
                    except S.json.JSONDecodeError as exc:
                        checks["reviews"] = S.R.selfcheck_entry(
                            "drift",
                            baseline=str(S._SELFCHECK_IMT),
                            reason="parse_error",
                            notes=[f"{host} returned HTTP 200 but invalid JSON: {exc}"],
                        )
                        review_payload = None
                    break
            if review_payload is None and used_host and "reviews" not in checks:
                checks["reviews"] = S.R.selfcheck_entry(
                    "drift",
                    baseline=str(S._SELFCHECK_IMT),
                    reason="schema_drift",
                    notes=[f"{used_host} returned null, not object"],
                )
            elif review_payload is None and "reviews" not in checks:
                checks["reviews"] = S.R.selfcheck_entry(
                    "inconclusive",
                    baseline=str(S._SELFCHECK_IMT),
                    reason="transport_down",
                    notes=[f"{a['host']} http {a['status']} err={a['error']}" for a in attempts],
                )
            if review_payload is None:
                pass
            else:
                if not isinstance(review_payload, dict):
                    checks["reviews"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=str(S._SELFCHECK_IMT),
                        reason="schema_drift",
                        notes=[f"{used_host or 'feedbacks'} returned {type(review_payload).__name__}, not object"],
                    )
                    review_payload = None
                if review_payload is None:
                    pass
                else:
                    if "feedbacks" not in review_payload:
                        checks["reviews"] = S.R.selfcheck_entry(
                            "drift",
                            baseline=str(S._SELFCHECK_IMT),
                            reason="schema_drift",
                            notes=["feedbacks key missing from 200 response"],
                        )
                    else:
                        fb = review_payload.get("feedbacks")
                    if "reviews" in checks and checks["reviews"].get("state") == "drift":
                        pass
                    elif not isinstance(fb, list):
                        checks["reviews"] = S.R.selfcheck_entry(
                            "drift",
                            baseline=str(S._SELFCHECK_IMT),
                            reason="schema_drift",
                            notes=[f"feedbacks is {type(fb).__name__}, not list"],
                        )
                    else:

                        def _has_review_text(f: dict) -> bool:
                            for key in ("text", "pros", "cons"):
                                value = f.get(key)
                                if isinstance(value, str) and value.strip():
                                    return True
                            return False

                        with_text = sum(1 for f in fb if isinstance(f, dict) and _has_review_text(f))
                        has_rating = any(isinstance(f, dict) and "productValuation" in f for f in fb)
                        if not fb:
                            # empty pool on a known-reviewed baseline is rot/transport, not drift
                            checks["reviews"] = S.R.selfcheck_entry(
                                "inconclusive",
                                baseline=str(S._SELFCHECK_IMT),
                                reason="baseline_gone",
                                notes=["empty feedbacks pool (baseline may have lost reviews)"],
                            )
                        elif with_text == 0 or not has_rating:
                            checks["reviews"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=str(S._SELFCHECK_IMT),
                                pool_size=len(fb),
                                with_text=with_text,
                                has_productValuation=has_rating,
                                notes=[
                                    "pool present but 0 texts or no productValuation key (content/rating key drift)"
                                ],
                            )
                        else:
                            checks["reviews"] = S.R.selfcheck_entry(
                                "healthy", baseline=str(S._SELFCHECK_IMT), pool_size=len(fb), with_text=with_text
                            )
    except (TimeoutError, Exception) as exc:
        checks["reviews"] = S.R.selfcheck_entry(
            "inconclusive",
            baseline=str(S._SELFCHECK_IMT),
            reason="timeout" if isinstance(exc, S.asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )


async def _probe_search_goods(checks: dict[str, dict], use_dom: bool) -> str:
    # --- search_goods (wb_search STEP 1 — the id list) ---
    sq = S._SELFCHECK_SEARCH_QUERY
    sg_params = S.httpx.QueryParams({"query": sq, "dest": S.WB_DEFAULT_DEST})
    sg_url = f"https://search-goods.wildberries.ru/search?{sg_params}"
    await S._polite_wait()
    try:
        async with S.asyncio.timeout(45):
            async with S._wb_client() as client:
                status_code, text, err = await S._fresh_get_text(client, sg_url)
            if err or status_code == 429:
                checks["search_goods"] = S.R.selfcheck_entry(
                    "inconclusive",
                    baseline=sq,
                    reason="rate_limited" if status_code == 429 else "transport_down",
                    notes=[f"http {status_code} err={err}"],
                )
            elif status_code != 200:
                checks["search_goods"] = S.R.selfcheck_entry(
                    "inconclusive", baseline=sq, reason="transport_down", notes=[f"http {status_code}"]
                )
            else:
                try:
                    ids = S.json.loads(text or "")
                except S.json.JSONDecodeError as exc:
                    checks["search_goods"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=sq,
                        reason="parse_error",
                        notes=[f"search-goods returned HTTP 200 but invalid JSON: {exc}"],
                    )
                    ids = None
                if ids is None:
                    if "search_goods" not in checks:
                        checks["search_goods"] = S.R.selfcheck_entry(
                            "drift", baseline=sq, reason="schema_drift", notes=["search-goods returned null, not list"]
                        )
                elif not isinstance(ids, list):
                    checks["search_goods"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=sq,
                        notes=[
                            f"search-goods returned {type(ids).__name__}, not a list "
                            "(response shape drift — wb_search would error/empty)"
                        ],
                    )
                elif not ids:
                    # An EMPTY list (vs a populated list we can't parse) is more likely a
                    # transient WB hiccup than a shape drift — inconclusive, not a false
                    # alarm (audit 2026-06-01 FALSE_POSITIVE).
                    checks["search_goods"] = S.R.selfcheck_entry(
                        "inconclusive",
                        baseline=sq,
                        reason="transport_down",
                        notes=[
                            "search-goods returned an empty list (transient / no results — cannot prove id-shape drift)"
                        ],
                    )
                else:
                    recovered = S._recover_search_ids(ids)
                    if not recovered:
                        # list HAS items but 0 recoverable ids = the real id-shape drift
                        # the wb_search id-recovery guard exists for. LOUD.
                        checks["search_goods"] = S.R.selfcheck_entry(
                            "drift",
                            baseline=sq,
                            raw_len=len(ids),
                            notes=[
                                "search-goods list has items but 0 recoverable numeric "
                                "ids (id-shape drift — see wb_search recovery guard)"
                            ],
                        )
                    else:
                        checks["search_goods"] = S.R.selfcheck_entry(
                            "healthy", baseline=sq, recovered_ids=len(recovered)
                        )
    except (TimeoutError, Exception) as exc:
        checks["search_goods"] = S.R.selfcheck_entry(
            "inconclusive",
            baseline=sq,
            reason="timeout" if isinstance(exc, S.asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )

    if use_dom:
        checks["search_goods"]["notes"].append("wb_search does not use this path while WB_TRANSPORT=dom")
    return sq


async def _probe_search_v9(checks: dict[str, dict], use_dom: bool, sq: str) -> None:
    # --- search_v9 (the configured wb_search path: API or rendered grid) ---
    #
    # In API mode, probed separately from search_goods, because they fail differently and only
    # this one is on the path a caller actually takes. When v9 was refused, search
    # fell through to the legacy id list and answered with unrelated products and
    # prices attached — and this canary stayed green, because its other probe reads
    # search-goods and never touched v9. A refused primary path has to be visible.
    if use_dom:
        checks["search_v9"] = await S._wb_dom_selfcheck("search")
    else:
        v9_params = S.httpx.QueryParams(
            {
                "appType": "1",
                "curr": "rub",
                "dest": S.WB_DEFAULT_DEST,
                "locale": "ru",
                "query": sq,
                "resultset": "catalog",
                "page": "1",
                "spp": "30",
            }
        )
        v9_url = f"https://search.wb.ru/exactmatch/ru/common/v9/search?{v9_params}"
        await S._polite_wait()
        try:
            async with S.asyncio.timeout(45):
                async with S._wb_client() as client:
                    status_code, text, err = await S._fresh_get_text(client, v9_url)
                if err or status_code == 429:
                    checks["search_v9"] = S.R.selfcheck_entry(
                        "inconclusive",
                        baseline=sq,
                        reason="rate_limited" if status_code == 429 else "transport_down",
                        notes=[f"http {status_code} err={err} — primary search path (v9) unavailable"],
                    )
                elif status_code != 200:
                    checks["search_v9"] = S.R.selfcheck_entry(
                        "inconclusive",
                        baseline=sq,
                        reason="transport_down",
                        notes=[f"http {status_code} — primary search path refused"],
                    )
                else:
                    try:
                        payload = S.json.loads(text or "")
                    except S.json.JSONDecodeError as exc:
                        checks["search_v9"] = S.R.selfcheck_entry(
                            "drift",
                            baseline=sq,
                            reason="parse_error",
                            notes=[f"v9 returned HTTP 200 but invalid JSON: {exc}"],
                        )
                        payload = None
                    if payload is not None:
                        v9_products: Any = None
                        if isinstance(payload, dict):
                            v9_products = payload.get("products")
                            if not isinstance(v9_products, list):
                                nested = payload.get("data")
                                if isinstance(nested, dict) and isinstance(nested.get("products"), list):
                                    v9_products = nested["products"]
                        if not isinstance(payload, dict):
                            checks["search_v9"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=sq,
                                reason="schema_drift",
                                notes=[f"v9 returned {type(payload).__name__}, not object"],
                            )
                        elif not isinstance(v9_products, list):
                            checks["search_v9"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=sq,
                                reason="schema_drift",
                                notes=["v9 200 body has no products list (wb_search would fall back to stale ids)"],
                            )
                        elif not v9_products:
                            checks["search_v9"] = S.R.selfcheck_entry(
                                "drift",
                                baseline=sq,
                                reason="empty_products",
                                notes=["v9 answered 200 with no products for an evergreen query"],
                            )
                        else:
                            identified = [
                                item
                                for item in v9_products
                                if isinstance(item, dict)
                                and (
                                    (isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool))
                                    or (isinstance(item.get("nmId"), int) and not isinstance(item.get("nmId"), bool))
                                )
                            ]
                            # R-SELFCHECK-GATES-PRICE: the search tool's own price extraction,
                            # not a second reader (v9 carries prices in sizes[].price, and the old
                            # salePriceU/priceU check counted 0 on every real page).
                            priced = [item for item in identified if S._extract_price_rub(item)[0] is not None]
                            if not identified:
                                checks["search_v9"] = S.R.selfcheck_entry(
                                    "drift",
                                    baseline=sq,
                                    raw_len=len(v9_products),
                                    notes=["v9 products carry no numeric id (id-shape drift)"],
                                )
                            else:
                                # Identifiable products are not enough: the
                                # search tool's own answer warns through the same
                                # required-family registry, and the canary must
                                # fail the same way when a whole parser-critical
                                # family vanishes.
                                drift = S.R.shape_drift_warning(
                                    {"products": v9_products}, S.SEARCH_REQUIRED_FAMILIES, source="wb_search"
                                )
                                if drift:
                                    checks["search_v9"] = S.R.selfcheck_entry(
                                        "drift",
                                        baseline=sq,
                                        reason="shape_drift",
                                        recovered_ids=len(identified),
                                        priced=len(priced),
                                        notes=[drift],
                                    )
                                elif len(identified) >= 3 and not priced:
                                    checks["search_v9"] = S.R.selfcheck_entry(
                                        "drift",
                                        baseline=sq,
                                        reason="no_prices",
                                        recovered_ids=len(identified),
                                        priced=0,
                                        notes=["v9 products carry no price the search tool can read"],
                                    )
                                else:
                                    checks["search_v9"] = S.R.selfcheck_entry(
                                        "healthy", baseline=sq, recovered_ids=len(identified), priced=len(priced)
                                    )
        except (TimeoutError, Exception) as exc:
            checks["search_v9"] = S.R.selfcheck_entry(
                "inconclusive",
                baseline=sq,
                reason="timeout" if isinstance(exc, S.asyncio.TimeoutError) else "transport_down",
                notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
            )


async def _probe_root_basket(checks: dict[str, dict], nm: int) -> None:
    # --- root_basket (wb_root_info imt_id resolution) ---
    await S._polite_wait()
    try:
        async with S.asyncio.timeout(45):
            host = S._basket_for_sku(nm)
            vol = nm // 100000
            part = nm // 1000
            basket_url = f"https://{host}/vol{vol}/part{part}/{nm}/info/ru/card.json"
            async with S._wb_client() as client:
                status_code, text, err = await S._fresh_get_text(client, basket_url)
            if err or status_code == 404 or status_code != 200:
                checks["root_basket"] = S.R.selfcheck_entry(
                    "inconclusive",
                    baseline=str(nm),
                    reason="transport_down",
                    notes=[f"basket http {status_code} err={err} host={host}"],
                )
            else:
                try:
                    data = S.json.loads(text or "")
                except S.json.JSONDecodeError as exc:
                    checks["root_basket"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        reason="parse_error",
                        notes=[f"basket 200 JSON parse error: {str(exc)[:120]}"],
                    )
                    data = None
                imt = None
                if isinstance(data, dict):
                    imt = S.R.first_present(data, "imt_id", "imtId")
                    if imt is None and isinstance(data.get("data"), dict):
                        imt = S.R.first_present(data["data"], "imt_id", "imtId")
                elif data is not None:
                    checks["root_basket"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        reason="schema_drift",
                        notes=[f"basket 200 root is {type(data).__name__}, not object"],
                    )
                # imt_id must be a usable POSITIVE INT (or int-like string) — a
                # drifted object/bool/empty/wrong-type value is unusable by
                # wb_reviews and is DRIFT, not healthy (audit FALSE_NEGATIVE).
                imt_int: int | None = None
                if isinstance(imt, bool):
                    imt_int = None
                elif isinstance(imt, int):
                    imt_int = imt
                elif isinstance(imt, str) and imt.strip().isdigit():
                    imt_int = int(imt.strip())
                if imt_int is None or imt_int <= 0:
                    checks["root_basket"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        notes=[
                            f"basket card.json 200 but imt_id unusable ({imt!r}) "
                            "(schema drift — wb_root_info + wb_reviews break)"
                        ],
                    )
                elif imt_int != S._SELFCHECK_IMT:
                    checks["root_basket"] = S.R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        imt_id=imt_int,
                        notes=[f"basket imt_id {imt_int} differs from known baseline {S._SELFCHECK_IMT}"],
                    )
                else:
                    checks["root_basket"] = S.R.selfcheck_entry(
                        "healthy", baseline=str(nm), imt_id=imt_int, matches_known=(imt_int == S._SELFCHECK_IMT)
                    )
    except (TimeoutError, Exception) as exc:
        checks["root_basket"] = S.R.selfcheck_entry(
            "inconclusive",
            baseline=str(nm),
            reason="timeout" if isinstance(exc, S.asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )


async def run(ctx: S.Context | None = None) -> S.WbSelfCheckResponse:
    use_dom = S._settings.transport == "dom"
    S.log_event("wb_selfcheck.start", transport=S._settings.transport)
    if ctx is not None:
        await ctx.info(
            "wb_selfcheck: probing card / reviews / search_goods / search_v9 / root_basket "
            f"(card/search_v9 transport={S._settings.transport})"
        )
    checks: dict[str, dict] = {}

    nm = await _probe_card(checks, use_dom)
    await _probe_reviews(checks)
    sq = await _probe_search_goods(checks, use_dom)
    await _probe_search_v9(checks, use_dom, sq)
    await _probe_root_basket(checks, nm)

    try:
        settings = S.get_settings()
        config_loaded = bool(settings)
    except Exception:
        config_loaded = False
    try:
        tool_count = len(await S.mcp.list_tools())
    except Exception:
        tool_count = 0

    result = S.R.selfcheck_result(
        "wb",
        checks,
        required=("card", "reviews", "search_goods", "search_v9", "root_basket"),
        server_version=S.SERVER_VERSION,
        server_started_at=S.SERVER_STARTED_AT,
        process_id=S.os.getpid(),
    )
    result["config_loaded"] = config_loaded
    result["tool_count"] = tool_count
    S.log_event("wb_selfcheck.done", status=result.get("status"), checks=len(checks))
    return S.WbSelfCheckResponse.model_validate(result)


# END_BLOCK_SELFCHECK
