"""Live smoke tests for the Yandex Market connector. Excluded from CI; see
wb-connector's test_live for the contract."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_yandex_selfcheck_reaches_a_verdict():
    from yandex_connector.server import yandex_selfcheck

    result = await yandex_selfcheck()

    assert result.status in ("success", "drift_detected", "inconclusive")
