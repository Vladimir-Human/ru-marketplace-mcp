"""The customs payment an Ozon card says is due on delivery.

Cross-border Ozon products show, under the checkout price, a line such as
"~8 167 ₽ доплата за таможенный платёж". The composer JSON carries it as a
``webIconWithText`` widget whose ``params.mode`` is ``customsDuty``; its
``textRs`` holds the amount (Ozon's estimate, marked "~") and the label
(recorded on the owner's machine, 2026-11-18). The price does not include it.
"""
# START_MODULE_CONTRACT
# PURPOSE: Read the customs payment an Ozon card shows beside its price, and word its warning.
# SCOPE: Pure functions over composer widgetStates; no I/O.
# DEPENDS: stdlib json; mcp_core.resilience (coerce_price).
# LINKS: M-OZON; R-OZON-CUSTOMS-DUTY.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# customs_duty: Whether a customsDuty widget is on the card, and its amount in roubles when readable.
# customs_warning: The customs_duty warning for ozon_card's meta.
# END_MODULE_MAP

from __future__ import annotations

import json
from typing import Any

from mcp_core import resilience as R


def customs_duty(widget_states: dict[str, Any]) -> tuple[bool, float | None]:
    """(shown, amount): shown is True when the card carries a customsDuty widget; amount is None when unreadable."""
    for key, raw in widget_states.items():
        if not key.startswith("webIconWithText"):
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        params = data.get("params") if isinstance(data, dict) else None
        if not isinstance(params, dict) or params.get("mode") != "customsDuty":
            continue
        for part in data.get("textRs") or []:
            content = part.get("content") if isinstance(part, dict) else None
            if isinstance(content, str) and "₽" in content:
                return True, R.coerce_price(content.replace("~", ""))
        return True, None
    return False, None


def customs_warning(amount: float | None) -> str:
    """Name the payment so an agent never quotes price as the total cost."""
    if amount is None:
        return "customs_duty: a customs payment is due on delivery, amount not stated; not in price"
    shown = f"{amount:,.0f}".replace(",", " ")
    return f"customs_duty: ~{shown} ₽ customs payment due on delivery (Ozon's estimate), not in price"
