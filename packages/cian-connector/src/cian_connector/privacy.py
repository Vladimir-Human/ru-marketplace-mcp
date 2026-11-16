"""Pure Cian publisher-name disclosure rule."""
# START_MODULE_CONTRACT
# PURPOSE: Keep organisation names while suppressing private publisher identity.
# SCOPE: Publisher's own flags and profile URI only; no viewer or nested agency, no I/O.
# DEPENDS: stdlib typing.
# LINKS: M-CIAN-PRIVACY; R-NO-PERSONAL-DATA.cian.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# public_publisher_name: Return the name only for an agency, developer or company profile.
# END_MODULE_MAP

from __future__ import annotations

from typing import Any

# START_BLOCK_PUBLISHER_NAME_PRIVACY


def public_publisher_name(publisher: dict[str, Any], name: Any) -> str | None:
    """Never mistake a specialist's nested agency or the viewer for the publisher."""
    uri = publisher.get("profileUri")
    organisation = (
        publisher.get("accountType") == "agency"
        or publisher.get("isDeveloper") is True
        or publisher.get("isBuilder") is True
        or publisher.get("userType") == "developer"
        or (isinstance(uri, str) and uri.startswith("company/"))
    )
    if organisation and isinstance(name, str):
        return name.strip() or None
    return None


# END_BLOCK_PUBLISHER_NAME_PRIVACY
