"""Live smoke tests for the Detsky Mir connector. Excluded from CI; see
wb-connector's test_live for the contract."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_detmir_selfcheck_reaches_a_verdict():
    from detmir_connector.server import detmir_selfcheck

    result = await detmir_selfcheck()

    assert result.status in ("success", "drift_detected", "inconclusive")
