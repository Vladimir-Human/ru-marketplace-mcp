"""Secret hygiene for the proxy setting.

The proxy URL may carry user:pass credentials, so it lives in a SecretStr:
any repr/str/model_dump of the settings must show the pydantic mask, never
the value — a settings dump that reaches a log or a tool error must not leak
it. Only the outbound fetch unwraps it (the connector's ``_proxy()``).
"""

from __future__ import annotations

import json

from lamoda_connector.settings import LamodaSettings
from pydantic import SecretStr

SECRET_PROXY = "http://user:p/ss@proxy.example:3128"


def test_the_proxy_secret_never_appears_in_settings_dumps():
    settings = LamodaSettings(proxy=SecretStr(SECRET_PROXY))

    assert SECRET_PROXY not in repr(settings)
    assert SECRET_PROXY not in str(settings)
    dumped = json.dumps(settings.model_dump(mode="json"))
    assert SECRET_PROXY not in dumped
    assert "**********" in dumped


def test_the_proxy_secret_is_still_available_to_the_fetch():
    settings = LamodaSettings(proxy=SecretStr(SECRET_PROXY))

    assert settings.proxy.get_secret_value() == SECRET_PROXY
