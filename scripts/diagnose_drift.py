"""Diagnose why a CDP source's search extractor found nothing.

``drift_detected`` says the page loaded and the parser did not understand it.
It does not say which of the three causes it was, and they need different
fixes:

  1. the page never rendered — a challenge wall, a login wall, a redirect;
  2. the page rendered but products arrive after our wait window;
  3. the page rendered fully and the anchor pattern the extractor keys on
     moved.

Only case 3 is a selector fix. Guessing between them is how a wrong "fix"
gets shipped, so this script asks the page directly and reports structure —
never page text, never the logged-in session's content. What comes back is
counts, tag and class stems, and a handful of sample URLs.

Run it from the machine whose Chrome holds the passed challenge:

    uv run python scripts/diagnose_drift.py citilink
    uv run python scripts/diagnose_drift.py megamarket
    uv run python scripts/diagnose_drift.py all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.parse

from mcp_core.resilience import shape_signature
from mcp_core.transport.chrome_cdp import NavBlocked, open_page

# source -> (search URL template, the anchor pattern its extractor keys on)
SOURCES: dict[str, tuple[str, str]] = {
    "citilink": ("https://www.citilink.ru/search/?text={q}", "/product/"),
    "dns": ("https://www.dns-shop.ru/search/?q={q}", "/product/"),
    "lamoda": ("https://www.lamoda.ru/catalogsearch/result/?q={q}", "/p/"),
    "taobao": ("https://s.taobao.com/search?q={q}", "item.taobao.com"),
}

# Megamarket is not in SOURCES: it answers JSON over CDP rather than rendering
# a page, so it gets its own probe instead of the DOM one.
API_SOURCES = ("megamarket",)

DEFAULT_QUERY = {
    "citilink": "ноутбук",
    "dns": "ноутбук",
    "lamoda": "кроссовки",
    "taobao": "手机",
    "megamarket": "стиральная машина",
}

# Structure only. No innerText, no attribute values beyond hrefs we already
# know are public catalog URLs — this page is running in a profile that may be
# logged in, and a diagnostic has no business reading that.
_DIAGNOSTIC_JS = """
(args) => {
    const out = {};
    out.title = (document.title || '').slice(0, 120);
    out.url = location.href.split('?')[0];
    out.ready = document.readyState;
    out.body_len = (document.body ? document.body.innerHTML.length : 0);
    out.anchors_total = document.querySelectorAll('a[href]').length;

    // Does the pattern the extractor keys on still exist?
    out.expected_pattern = args.pattern;
    out.expected_hits = document.querySelectorAll(
        'a[href*="' + args.pattern + '"]'
    ).length;

    // Which URL shapes DO appear? Group hrefs by their first two path
    // segments; the real product route shows up as the biggest bucket.
    const buckets = {};
    for (const a of document.querySelectorAll('a[href]')) {
        let p;
        try { p = new URL(a.href, location.href).pathname; } catch (e) { continue; }
        const seg = p.split('/').filter(Boolean).slice(0, 2).join('/');
        if (!seg) continue;
        buckets[seg] = (buckets[seg] || 0) + 1;
    }
    out.path_buckets = Object.entries(buckets)
        .sort((a, b) => b[1] - a[1]).slice(0, 12)
        .map(([k, v]) => ({ path: '/' + k, count: v }));

    // Repeated class stems point at the tile container even when the route
    // changed: a grid of N products is N elements sharing one stem.
    const stems = {};
    for (const el of document.querySelectorAll('[class]')) {
        const cls = typeof el.className === 'string' ? el.className : '';
        for (const c of cls.split(/\\s+/)) {
            if (!c || c.length < 4) continue;
            const stem = c.split(/[-_]/)[0].toLowerCase();
            if (stem.length < 4) continue;
            stems[stem] = (stems[stem] || 0) + 1;
        }
    }
    out.class_stems = Object.entries(stems)
        .sort((a, b) => b[1] - a[1]).slice(0, 12)
        .map(([k, v]) => ({ stem: k, count: v }));

    // When a page ships zero product anchors it is usually not missing the
    // products — it is a client-rendered app holding them as JSON. Count the
    // embedded state blobs and how product-shaped their contents look, so
    // "SELECTOR MOVED" can be told apart from "there are no anchors to select".
    const state = [];
    for (const key of ['__NUXT__', '__INITIAL_STATE__', '__NEXT_DATA__', '__APOLLO_STATE__']) {
        if (window[key] !== undefined) state.push({ where: 'window.' + key, kind: typeof window[key] });
    }
    for (const s of document.querySelectorAll('script[type="application/json"], script[type="application/ld+json"]')) {
        const len = (s.textContent || '').length;
        if (len > 200) state.push({ where: s.id || s.type, kind: 'script', chars: len });
    }
    out.embedded_state = state.slice(0, 8);

    // How many things on this page smell like a product record?
    let productish = 0;
    const scan = (o, depth) => {
        if (!o || depth > 4 || productish > 400) return;
        if (Array.isArray(o)) { for (const v of o.slice(0, 60)) scan(v, depth + 1); return; }
        if (typeof o !== 'object') return;
        const k = Object.keys(o).map(x => x.toLowerCase());
        const hasPrice = k.some(x => x.includes('price'));
        const hasId = k.some(x => x === 'sku' || x === 'id' || x.includes('productid'));
        if (hasPrice && hasId) productish++;
        for (const v of Object.values(o).slice(0, 40)) scan(v, depth + 1);
    };
    for (const key of ['__NUXT__', '__INITIAL_STATE__', '__NEXT_DATA__']) {
        if (window[key] !== undefined) { try { scan(window[key], 0); } catch (e) {} }
    }
    out.productish_records = productish;

    // Challenge / wall markers. Presence means "not a selector problem".
    const t = (document.body ? document.body.innerText : '').slice(0, 4000);
    out.walls = {
        qrator: /qrator|проверка браузера|checking your browser/i.test(t),
        captcha: /captcha|подтвердите|robot|не робот/i.test(t),
        login: /войти|log ?in|sign ?in|登录/i.test(t) && out.anchors_total < 40,
        empty_results: /ничего не найдено|нет результатов|no results|по вашему запросу/i.test(t),
    };
    return JSON.stringify(out);
}
"""


def _verdict(d: dict) -> tuple[str, str]:
    """Turn the raw structure into the one sentence the operator needs."""
    walls = d.get("walls", {})
    hits = d.get("expected_hits", 0)
    if hits > 0:
        return (
            "NOT DRIFT",
            f"the pattern {d['expected_pattern']!r} still matches {hits} anchors — "
            "the extractor should have found these. Look at the JS body, the wait window, or the body cap.",
        )
    if walls.get("qrator") or walls.get("captcha"):
        return (
            "WALL",
            "a challenge is on screen, so nothing was ever rendered. Pass it manually in this "
            "profile once, then re-run. This is not a selector problem.",
        )
    if walls.get("login"):
        return ("LOGIN", "the page wants a session. Log into this site in the scraping profile, then re-run.")
    if walls.get("empty_results"):
        return (
            "EMPTY",
            "the site says it found nothing for this query. Try another query before touching selectors — "
            "and note this is the case DNS/Citilink currently misreport as drift.",
        )
    if d.get("body_len", 0) < 5000:
        return ("NOT RENDERED", "the document is nearly empty — a redirect or a client-side app that never booted.")
    if d.get("productish_records", 0) > 5:
        return (
            "DATA NOT IN DOM",
            f"the page carries {d['productish_records']} product-shaped records in embedded state but no "
            "matching anchors. Scraping the DOM is the wrong approach here — read the state blob or the "
            "API behind it instead of hunting for a selector that does not exist.",
        )
    if d.get("anchors_total", 0) > 40:
        return (
            "SELECTOR MOVED",
            "the page is populated with links but none match the expected pattern. "
            "The real route is the top path bucket below — that is the selector fix.",
        )
    return ("UNCLEAR", "the page rendered but carries few links. Inspect it by hand in the same profile.")


async def diagnose_megamarket(query: str) -> int:
    """Megamarket answers JSON, not HTML, so the DOM probe cannot see it.

    Its drift means the connector reached a 200 and found no items array. The
    useful question is which: a ServicePipe refusal wearing a 200, or a renamed
    field. Reuse the connector's own transport so this measures the real path,
    then fingerprint the envelope with shape_signature — paths and types, no
    values, nothing from the logged-in session.
    """
    print(f"\n{'=' * 72}\nmegamarket  ·  mobile API via CDP\n{'=' * 72}")
    try:
        from megamarket_connector import server as mm
    except ImportError as exc:
        print(f"  megamarket-connector is not installed here: {exc}")
        return 1

    try:
        status, text = await mm._cdp_post_json("/catalogService/catalog/search", {"text": query, "page": 1}, None)
    except Exception as exc:
        print(f"  request failed: {type(exc).__name__}: {exc}")
        print("  Is Chrome running and has megamarket.ru passed ServicePipe in it?")
        return 1

    print(f"  HTTP {status}   {len(text):,} chars")
    if status != 200:
        print(f"  VERDICT: TRANSPORT — the API refused with {status}, nothing to parse.")
        return 2

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print(f"  body is not JSON. First 200 chars: {text[:200]!r}")
        print("  VERDICT: WALL — a challenge page came back where JSON was expected.")
        return 2

    if mm._is_ip_block(payload):
        print("  VERDICT: TRANSPORT — ServicePipe code-7 IP refusal, not a parser problem.")
        print("  Open megamarket.ru in the scraping profile and let the challenge pass.")
        return 2

    items, total, found = mm._parse_items(payload)
    print(f"  top-level keys   {sorted(payload)[:12]}")
    print(f"  items container  {'found' if found else 'NOT FOUND'}   parsed {len(items)}   total={total}")

    print("\n  response shape (paths and types, values dropped):")
    for path in shape_signature(payload, max_depth=4)[:22]:
        print(f"    {path}")

    if found and items:
        print("\n  VERDICT: NOT DRIFT — the parser understands this payload.")
        return 0
    if found:
        print("\n  VERDICT: EMPTY — the array is there and empty. Try another query.")
        return 2
    print(
        "\n  VERDICT: SHAPE MOVED — no list under items/goods/products/data.items.\n"
        "  Find the array in the shape above and add its key to _parse_items."
    )
    return 2


async def diagnose(source: str, query: str) -> int:
    if source == "megamarket":
        return await diagnose_megamarket(query)
    url_tpl, pattern = SOURCES[source]
    url = url_tpl.format(q=urllib.parse.quote(query))
    print(f"\n{'=' * 72}\n{source}  ·  {url}\n{'=' * 72}")

    try:
        async with open_page(url, wait_ms=9000) as page:
            raw = await asyncio.wait_for(page.evaluate(_DIAGNOSTIC_JS, {"pattern": pattern}), timeout=30.0)
    except NavBlocked as exc:
        print(f"  navigation refused: HTTP {exc.status}")
        print("  VERDICT: WALL — the request never reached a rendered page.")
        return 1
    except Exception as exc:
        print(f"  could not reach the page: {type(exc).__name__}: {exc}")
        print("  Is Chrome running with --remote-debugging-port=9222?")
        return 1

    d = json.loads(raw)
    label, advice = _verdict(d)

    print(f"  title        {d['title']!r}")
    print(f"  readyState   {d['ready']}   body {d['body_len']:,} chars   {d['anchors_total']} links")
    print(f"  expected     a[href*={d['expected_pattern']!r}]  ->  {d['expected_hits']} hits")
    active = [k for k, v in d.get("walls", {}).items() if v]
    print(f"  walls        {', '.join(active) if active else 'none detected'}")

    print("\n  link routes actually on the page:")
    for b in d.get("path_buckets", []) or [{"path": "(none)", "count": 0}]:
        mark = "  <-- expected" if pattern.strip("/") in b["path"] else ""
        print(f"    {b['count']:>5}  {b['path']}{mark}")

    print("\n  repeated class stems (tile container is usually near the top):")
    for s in d.get("class_stems", [])[:8]:
        print(f"    {s['count']:>5}  {s['stem']}")

    print(f"\n  shape: {len(shape_signature(d))} structural paths captured")
    print(f"\n  VERDICT: {label}\n  {advice}")
    return 0 if label == "NOT DRIFT" else 2


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", choices=[*SOURCES, *API_SOURCES, "all"])
    ap.add_argument("--query", default=None, help="search term (per-source default otherwise)")
    args = ap.parse_args()

    targets = [*SOURCES, *API_SOURCES] if args.source == "all" else [args.source]
    worst = 0
    for name in targets:
        rc = await diagnose(name, args.query or DEFAULT_QUERY[name])
        worst = max(worst, rc)
    print(f"\n{'=' * 72}")
    print("Run this from the Chrome profile that already passed each site's challenge.")
    print("A SELECTOR MOVED verdict is a real fix: update the anchor pattern and add a fixture.")
    return worst


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
