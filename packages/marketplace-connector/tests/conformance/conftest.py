"""Every registry source is a named parameter for future conformance rules.

START_MODULE_CONTRACT
PURPOSE: Supply one shared source fixture without importing connector servers.
SCOPE: Every mcp_core.sources entry, including profiles; no existing tests moved.
DEPENDS: pytest; mcp_core.sources.
LINKS: M-ARCH-GATE, M-SOURCES, R-ARCH-GATE.
END_MODULE_CONTRACT
START_MODULE_MAP
  source: Yield the registry entry named by this pytest parameter.
END_MODULE_MAP
"""

from collections.abc import Iterator

import pytest
from mcp_core.sources import SOURCES, SourceSpec

# START_BLOCK_REGISTRY_FIXTURE


@pytest.fixture(params=SOURCES, ids=lambda source: source.name)
def source(request: pytest.FixtureRequest) -> Iterator[SourceSpec]:
    yield request.param


# END_BLOCK_REGISTRY_FIXTURE
