"""Close a raw-CDP tab and confirm Chrome dropped it.

Chrome can answer ``Target.closeTarget`` with ``success: true`` and keep the
tab. The CDP simulator (scenario S4, 2026-11-17) caught it: a Citilink 429
whose body never finished left the raw-CDP tab open on Linux and Windows for
as long as the test watched, while macOS closed it. A second close removed
it every time (3 of 3 local runs). So the close is confirmed against
``Target.getTargets`` and repeated a few times, all inside one deadline.
"""
# START_MODULE_CONTRACT
# PURPOSE: Close one raw-CDP page target and confirm it is gone, with a bounded retry.
# SCOPE: Target.closeTarget plus a Target.getTargets check over an injected browser-session send.
# DEPENDS: stdlib asyncio,collections.abc,typing.
# LINKS: M-RAW-TARGETS, M-CHROME-CDP, R-CDP-SIM-S4.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# close_target: Close a target, re-check the target list, ask again until it is gone or attempts run out.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

Send = Callable[..., Awaitable[Any]]

CLOSE_ATTEMPTS = 4
RECHECK_S = 0.25


# START_CONTRACT: close_target
# PURPOSE: Leave no raw-CDP tab behind after a call, even when Chrome ignores the first close.
# INPUTS: send(method, params, timeout=...) on the browser session; the page target id; a per-command timeout.
# OUTPUTS: True when the target list no longer holds the id; False when attempts ran out or a command failed.
# SIDE_EFFECTS: Up to CLOSE_ATTEMPTS Target.closeTarget and Target.getTargets commands; never raises.
# END_CONTRACT: close_target
async def close_target(send: Send, target_id: str, *, timeout_s: float, recheck_s: float = RECHECK_S) -> bool:
    """Close ``target_id`` and ask again while ``Target.getTargets`` still lists it."""
    for _ in range(CLOSE_ATTEMPTS):
        try:
            await send("Target.closeTarget", {"targetId": target_id}, timeout=timeout_s)
            await asyncio.sleep(recheck_s)
            listed = await send("Target.getTargets", {}, timeout=timeout_s)
        except Exception:
            return False
        infos = listed.get("targetInfos") if isinstance(listed, dict) else None
        if not isinstance(infos, list):
            return False
        if all(not isinstance(info, dict) or info.get("targetId") != target_id for info in infos):
            return True
    return False
