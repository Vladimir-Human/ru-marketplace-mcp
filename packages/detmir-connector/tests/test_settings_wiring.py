"""The dead wall-timeout knob is gone (R-SETTINGS-WIRED).

``DETMIR_WALL_TIMEOUT`` promised «a hard ceiling for a whole tool call,
including retries» and nothing read it: the connector had no whole-call
ceiling at all, so the promise was a knob that did nothing. The shared HTTP
tier's ``get_text_with_retries`` bounds the NUMBER of attempts, not the time
they take, and its budgeted sibling never retries the 502/503/504 statuses
Detsky Mir relies on — so the knob could not be wired without either a new
parameter in the shared runtime or a change in the default behaviour. It is
deleted instead, and the per-request timeout that does work stays.
"""

from __future__ import annotations

import pytest
from detmir_connector import server
from detmir_connector.settings import DetmirSettings


@pytest.mark.req("R-SETTINGS-WIRED")
def test_the_unwired_wall_timeout_knob_is_gone():
    assert "wall_timeout" not in DetmirSettings.model_fields


@pytest.mark.req("R-SETTINGS-WIRED")
async def test_the_per_request_timeout_reaches_the_http_client(monkeypatch):
    """The timeout that IS wired stays wired: it is the client's own budget."""
    captured: dict[str, object] = {}

    def fake_build_client(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop here: the client was built")

    monkeypatch.setenv("DETMIR_TIMEOUT", "12")
    monkeypatch.setattr(server, "_settings", DetmirSettings())
    monkeypatch.setattr(server, "build_client", fake_build_client)

    with pytest.raises(RuntimeError):
        await server._fetch_json("https://www.detsky.ru/x", "card", None)

    assert captured["timeout_s"] == 12.0
