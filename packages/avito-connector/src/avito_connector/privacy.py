"""Pure Avito seller-name disclosure rule."""
# START_MODULE_CONTRACT
# PURPOSE: Keep marketplace-labelled companies and shop names, not private identity.
# SCOPE: Name selection only; no I/O, no changes to publisher flags, URLs or ratings.
# DEPENDS: stdlib re,urllib.parse; mcp_core.resilience.
# LINKS: M-AVITO-PRIVACY; R-NO-PERSONAL-DATA.avito.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# public_seller_name: Return a name for a company or an Avito /brands/ shop, otherwise None.
# END_MODULE_MAP

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from mcp_core.resilience import flatten_text

# START_BLOCK_SELLER_NAME_PRIVACY


def public_seller_name(name: Any, is_company: bool | None, profile_url: str | None) -> str | None:
    """A /brands/ shop qualifies even when Avito labels it a private person."""
    shop = False
    if profile_url:
        try:
            parts = urlsplit(profile_url)
            shop = (
                parts.scheme in ("", "http", "https")
                and (not parts.netloc or parts.hostname in {"avito.ru", "www.avito.ru"})
                and parts.username is None
                and parts.password is None
                and parts.port in (None, 80, 443)
                and re.fullmatch(r"/brands/[0-9A-Za-z_-]+/?", parts.path) is not None
            )
        except ValueError:
            pass
    return flatten_text(name) if is_company is True or shop else None


# END_BLOCK_SELLER_NAME_PRIVACY
