"""P2-1 m5/m6 across Yandex's committed SSR, zone and schema.org captures."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve regular prices and ids while seller fields carry hostile instructions.
# SCOPE: Existing pure SSR parsers; product titles/descriptions, review text and shop names only.
# DEPENDS: pytest; mcp_core.testing.capture_mutation; existing capture parser adapter.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.conditional-price; R-CAPTURE-MUTATION.seller-text; R-CAPTURE-MUTATION.split-payment.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# TEXT_PATHS/_seller_field: Separate seller fields from vendor names, UI headings and schema.org metadata.
# test_capture_m5_m6_task_facts: Fifty seeds per capture and family; empty shells are no-target controls.
# END_MODULE_MAP

import pytest
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_preserving_mutations

from . import test_capture_mutation as capture

# START_BLOCK_CAPTURE_MUTATIONS
TEXT_PATHS = (
    r"(?:^|\.)collections\.(?:product|offer)\.[^.]+\.titles\.raw$",
    r"(?:^|\.)collections\.productSnippet\.[^.]+\.payload\.title\.value$",
    r"(?:^|\.)collections\.titleV2\.[^.]+\.title$",
    r"(?:^|\.)collections\.(?:shop|shopInfo|businessStats)\.[^.]+\.(?:name|shopName)$",
    r"(?:^|\.)collections\.reviews\.reviews\[\]\.(?:pro|contra|comment)$",
    r"^(?:itemListElement\[\]\.item\.)?(?:name|description)$",
    r"^title$",
    r"^signals\[\]\.title$",
)
TEXT_NODES = (r"^<h1(?:\s|>)",)
# These committed controls contain no product or seller-authored text to attack.
TEXTLESS_CONTROLS = {"search_empty.html", "card_empty_shell.html"}
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _seller_field(parent: dict, key: str) -> bool:
    if key in {"name", "description"} and "@type" in parent:
        return parent["@type"] == "Product"
    if key == "title" and "type" in parent:
        return parent["type"] in {"offer", "shop"}
    return True


@pytest.mark.req(
    "R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.seller-text", "R-CAPTURE-MUTATION.split-payment"
)
@pytest.mark.parametrize("name", capture.CASES)
@pytest.mark.parametrize("mutation", ("m5", "m6a", "m6e"))
def test_capture_m5_m6_task_facts(name: str, mutation: str) -> None:
    run_preserving_mutations(
        (capture.FIXTURES / name).read_text(encoding="utf-8"),
        lambda raw: capture._parse(name, raw),
        fixture=name,
        mutation=mutation,
        kind="html",
        price_paths=capture.PRICE_PATHS,
        text_paths=TEXT_PATHS,
        text_nodes=TEXT_NODES,
        text_guard=_seller_field,
        price_fields=capture.PRICE_FIELDS if mutation == "m6a" else (capture.PRICE_FIELDS[0],),
        id_fields=capture.ID_FIELDS,
        seeds=PYTHON_SEEDS,
        exclusions=EXCLUSIONS,
        require_seller_target=name not in TEXTLESS_CONTROLS,
    )


# END_BLOCK_CAPTURE_MUTATIONS
