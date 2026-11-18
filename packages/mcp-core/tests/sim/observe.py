"""Independent CDP observations for real simulator targets, not browser fakes.

START_MODULE_CONTRACT
PURPOSE: Observe target identity/lifecycle and operate only test-owned targets outside the transport under test.
SCOPE: Real loopback CDP commands/events, bounded reads, owned-tab creation/closure and exact document snapshots.
DEPENDS: websockets.asyncio.client; stdlib asyncio,http.client,json,contextlib,urllib.parse; sibling chrome.
LINKS: M-CDP-SIM, R-CDP-SIM-S5, R-CDP-SIM-S6, R-CDP-SIM-S8, R-CDP-SIM-S10.
END_MODULE_CONTRACT
START_MODULE_MAP
  Channel: Demultiplex actual CDP replies and retain events independently of product CDP dispatch.
  browser_channel: Discover the disposable browser's websocket using its loopback HTTP endpoint.
  target_info: Ask Chrome for one target's current identity, URL and title.
  owned_tab: Create and clean up only the target returned by this test's createTarget call.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import http.client
import json
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

# START_BLOCK_INDEPENDENT_CDP


class Channel:
    def __init__(self, ws):
        self.ws = ws
        self.events = []
        self.pending = {}
        self.sequence = 0
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if "method" in message:
                    self.events.append(message)
                future = self.pending.get(message.get("id"))
                if future is not None and not future.done():
                    future.set_result(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("the independent CDP observer disconnected"))

    async def send(self, method, params=None):
        self.sequence += 1
        identifier = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            await self.ws.send(json.dumps({"id": identifier, "method": method, "params": params or {}}))
            async with asyncio.timeout(5):
                message = await future
            assert "error" not in message, f"independent CDP command failed: {method}: {message.get('error')}"
            return message.get("result", {})
        finally:
            self.pending.pop(identifier, None)

    async def close(self):
        self.reader.cancel()
        with suppress(asyncio.CancelledError):
            await self.reader


def _browser_url(chrome):
    connection = http.client.HTTPConnection("127.0.0.1", chrome.port, timeout=3)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        assert response.status == 200
        advertised = json.loads(response.read())["webSocketDebuggerUrl"]
        path = urlsplit(advertised).path
        assert path.startswith("/devtools/browser/")
        return f"ws://127.0.0.1:{chrome.port}{path}"
    finally:
        connection.close()


@asynccontextmanager
async def browser_channel(chrome):
    url = await asyncio.to_thread(_browser_url, chrome)
    async with connect(url, open_timeout=3, proxy=None) as ws:
        channel = Channel(ws)
        try:
            await channel.send("Target.setDiscoverTargets", {"discover": True})
            yield channel
        finally:
            await channel.close()


async def target_info(browser, target_id):
    info = await browser.send("Target.getTargetInfo", {"targetId": target_id})
    target = info["targetInfo"]
    return {key: target[key] for key in ("targetId", "url", "title")}


@asynccontextmanager
# START_CONTRACT: owned_tab
# PURPOSE: Create a real external tab, wait for its document and close exactly that target on every exit.
# INPUTS: The disposable simulator, its independent browser channel and an explicitly routed loopback-mapped URL.
# OUTPUTS: The exact created target id, live page-event observer and actual Chrome identity snapshot.
# SIDE_EFFECTS: CDP create/navigate/observe/close; no product lifecycle hook, URL-based cleanup or fixture mutation.
# END_CONTRACT: owned_tab
async def owned_tab(chrome, browser, url):
    created = await browser.send("Target.createTarget", {"url": "about:blank", "background": True})
    target_id = created["targetId"]
    try:
        async with connect(f"ws://127.0.0.1:{chrome.port}/devtools/page/{target_id}", open_timeout=3, proxy=None) as ws:
            page = Channel(ws)
            try:
                await page.send("Page.enable")
                await page.send("Network.enable")
                await page.send("Inspector.enable")
                navigated = await page.send("Page.navigate", {"url": url})
                assert "errorText" not in navigated
                async with asyncio.timeout(5):
                    while True:
                        snapshot = await target_info(browser, target_id)
                        state = await page.send(
                            "Runtime.evaluate",
                            {
                                "expression": "({ready: document.readyState, title: document.title})",
                                "returnByValue": True,
                            },
                        )
                        val = state["result"].get("value") or {}
                        doc_title = val.get("title") or ""
                        if (
                            snapshot["url"] == url
                            and val.get("ready") == "complete"
                            and (not doc_title or snapshot["title"] == doc_title)
                        ):
                            break
                        await asyncio.sleep(0.05)
                yield target_id, page, snapshot
            finally:
                await page.close()
    finally:
        # Do not close any target found by URL, arrival order or broad tab scans.
        await browser.send("Target.closeTarget", {"targetId": target_id})


# END_BLOCK_INDEPENDENT_CDP
