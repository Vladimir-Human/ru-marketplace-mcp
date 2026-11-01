"""Live smoke tests for the Detsky Mir connector. Excluded from CI; see
wb-connector's test_live for the contract."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_detmir_selfcheck_reaches_a_verdict():
    from detmir_connector.server import detmir_selfcheck

    result = await detmir_selfcheck()

    if result.status == "inconclusive":
        pytest.skip("selfcheck inconclusive: the source could not be judged from here (block, challenge or network)")
    assert result.status == "success", f"selfcheck verdict: {result.status}"
