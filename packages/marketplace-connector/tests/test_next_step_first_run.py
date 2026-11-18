"""Doctor next steps the first-run probe found wrong or empty (fresh profile, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove the R-DOCTOR-NEXT-STEP additions: real evidence (a wall) names the step before a budget refusal, and a
         browser source whose page did not load while Chrome answered gets the unblock command, not a bare doc link.
SCOPE: cli.next_step, pure, with the selfcheck results the probe's doctor printed.
DEPENDS: pytest; marketplace_connector.cli.
LINKS: M-MARKETPLACE; R-DOCTOR-NEXT-STEP; plan P3-1.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import pytest
from marketplace_connector import cli

CHECKOUT = cli.INSTALL_CHECKOUT


@pytest.mark.req("R-DOCTOR-NEXT-STEP")
def test_a_wall_beside_a_budget_refusal_names_unblock() -> None:
    # The probe's Ozon row: the card answered 403, the reviews checks were refused by the budget.
    checks = {
        "search": {"state": "healthy"},
        "card": {"state": "inconclusive", "reason": "blocked"},
        "reviews": {"state": "inconclusive", "reason": "transport_down"},
        "budget": {"state": "inconclusive", "reason": "budget_refused"},
    }

    step = cli.next_step("ozon", "inconclusive", checks, cdp_reachable=True, install_mode=CHECKOUT)

    assert step == cli.NEXT_STEP_BROWSER_WALL.format(source="ozon")


@pytest.mark.req("R-DOCTOR-NEXT-STEP")
def test_a_budget_refusal_alone_still_says_it_judges_nothing() -> None:
    checks = {"search": {"state": "healthy"}, "budget": {"state": "inconclusive", "reason": "budget_refused"}}

    step = cli.next_step("ozon", "inconclusive", checks, cdp_reachable=True, install_mode=CHECKOUT)

    assert step is not None and "budget refused" in step


@pytest.mark.req("R-DOCTOR-NEXT-STEP")
@pytest.mark.parametrize("source", ["lamoda", "citilink"])
def test_a_browser_page_that_did_not_load_with_chrome_up_names_unblock(source: str) -> None:
    checks = {"search": {"state": "inconclusive", "reason": "transport_down"}}

    step = cli.next_step(source, "inconclusive", checks, cdp_reachable=True, install_mode=CHECKOUT)

    assert step is not None
    assert f"uv run marketplace-mcp unblock {source}" in step
    assert "ANTI_BOT" not in step


@pytest.mark.req("R-DOCTOR-NEXT-STEP")
@pytest.mark.parametrize(
    "source,cdp_reachable",
    [
        ("citilink", None),  # the probe could not tell: no browser advice on a guess
        ("yandex_market", True),  # not read through the browser
    ],
)
def test_without_a_reachable_browser_source_the_doc_link_stays(source: str, cdp_reachable: bool | None) -> None:
    checks = {"search": {"state": "inconclusive", "reason": "transport_down"}}

    step = cli.next_step(source, "inconclusive", checks, cdp_reachable=cdp_reachable, install_mode=CHECKOUT)

    assert step == cli.NEXT_STEP_ANTIBOT
