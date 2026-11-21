"""AVITO_MAX_PHOTOS: the photo cap is an operator setting (R-AVITO-MAX-PHOTOS).

The cap was a constant (3). An operator who reviews used listings may want
more without patching the server, while everyone else keeps today's default.
The same value must be the schema's advertised maximum and the loop's cap, so
a fresh import is the only honest check of the advertised side: the schema is
built when the module is imported, as the other AVITO_ settings are read.

LINKS: M-AVITO; R-AVITO-MAX-PHOTOS; R-TOOL-SCHEMA.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from avito_connector.settings import AvitoSettings
from pydantic import ValidationError

_PROBE = """
import asyncio, json
from avito_connector import server

async def main():
    tool = next(t for t in await server.mcp.list_tools() if t.name == "avito_card")
    photos = tool.parameters["properties"]["photos"]
    print(json.dumps({"maximum": photos["maximum"], "default": photos["default"], "cap": server._MAX_PHOTOS}))

asyncio.run(main())
"""


def _fresh_import(max_photos: str | None) -> dict[str, int]:
    env = {k: v for k, v in os.environ.items() if k != "AVITO_MAX_PHOTOS"}
    if max_photos is not None:
        env["AVITO_MAX_PHOTOS"] = max_photos
    done = subprocess.run(
        [sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True, timeout=120, check=True
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.req("R-AVITO-MAX-PHOTOS.wired")
def test_the_setting_is_the_advertised_maximum_and_the_loop_cap():
    assert _fresh_import("10") == {"maximum": 10, "default": 0, "cap": 10}


@pytest.mark.req("R-AVITO-MAX-PHOTOS.wired")
def test_unset_keeps_three_in_the_schema_and_the_loop():
    assert _fresh_import(None) == {"maximum": 3, "default": 0, "cap": 3}


@pytest.mark.req("R-AVITO-MAX-PHOTOS.wired")
def test_the_default_is_the_cap_the_tool_had_before(monkeypatch):
    monkeypatch.delenv("AVITO_MAX_PHOTOS", raising=False)

    assert AvitoSettings().max_photos == 3


@pytest.mark.req("R-AVITO-MAX-PHOTOS.wired")
@pytest.mark.parametrize("value", [-1, 11])
def test_a_cap_outside_zero_to_ten_is_refused(value):
    with pytest.raises(ValidationError):
        AvitoSettings(max_photos=value)
