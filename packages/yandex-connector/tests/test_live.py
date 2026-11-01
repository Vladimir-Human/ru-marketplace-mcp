"""Live smoke tests for the Yandex Market connector. Excluded from CI; see
wb-connector's test_live for the contract."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_yandex_selfcheck_reaches_a_verdict():
    from yandex_connector.server import yandex_selfcheck

    result = await yandex_selfcheck()

    if result.status == "inconclusive":
        pytest.skip("selfcheck inconclusive: the source could not be judged from here (block, challenge or network)")
    assert result.status == "success", f"selfcheck verdict: {result.status}"
