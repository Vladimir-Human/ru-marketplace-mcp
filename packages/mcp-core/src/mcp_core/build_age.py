"""Local release-age information; never check whether a newer release exists."""
# START_MODULE_CONTRACT
# PURPOSE: Describe an old installed build from its own release metadata.
# SCOPE: Strict greater-than-30-day age check and conditional upgrade advice; no I/O except the local date.
# DEPENDS: mcp_core release constants; stdlib datetime,typing.
# LINKS: M-BUILD-AGE; R-OLD-BUILD-INFO.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# OldBuildInfo: The four informational fields shared by tool errors and doctor.
# old_build_info: Return local age information, or None for a young/unknown build.
# END_MODULE_MAP

from __future__ import annotations

import datetime as dt
from typing import TypedDict

import mcp_core

# START_BLOCK_BUILD_AGE


class OldBuildInfo(TypedDict):
    version: str
    released: str
    age_days: int
    upgrade: str


def old_build_info(today: dt.date | None = None) -> OldBuildInfo | None:
    """Return informational advice only when the release is older than 30 days."""
    try:
        released = dt.date.fromisoformat(mcp_core.__release_date__)
    except ValueError:
        return None
    age_days = ((today if today is not None else dt.date.today()) - released).days
    if age_days <= 30:
        return None
    return {
        "version": mcp_core.__version__,
        "released": mcp_core.__release_date__,
        "age_days": age_days,
        "upgrade": "if a newer version exists: uvx ru-marketplace-mcp@latest (or: pip install -U ru-marketplace-mcp)",
    }


# END_BLOCK_BUILD_AGE
