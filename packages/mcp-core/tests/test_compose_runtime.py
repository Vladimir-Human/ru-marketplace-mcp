"""Exercise every Compose service's merged HTTP settings against the runtime.

YAML anchors are resolved by the YAML parser. Required Compose substitutions
are asserted explicitly, then filled with fixture credentials; no Docker daemon
or network is needed to catch a deployment that fails the startup auth gate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from mcp_core import runtime

_ROOT = Path(__file__).resolve().parents[3]
_SERVICES = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("service_name", sorted(_SERVICES))
def test_compose_service_passes_http_startup_auth_gate(service_name, monkeypatch):
    service = _SERVICES[service_name]
    environment = {key: str(value) for key, value in service["environment"].items()}
    credentials = {
        runtime.ENV_HTTP_AUTH_TOKEN: "compose-test-token",
        runtime.ENV_HTTP_TENANT_ID: "compose-test-tenant",
    }
    for key, value in credentials.items():
        # :? rejects both an unset variable and an explicitly empty one. Do not
        # permit a checked-in credential or a fallback that silently disables auth.
        assert re.fullmatch(r"\$\{" + key + r":\?[^}]+\}", environment[key])
        environment[key] = value

    for key in os.environ:
        if key.startswith("MCP_HTTP_") or key == runtime.ENV_TRANSPORT:
            monkeypatch.delenv(key)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    server = Mock()
    assert runtime.run_server(server, server_name=service_name) == 0
    server.run.assert_called_once_with(transport="http", host="0.0.0.0", port=8000, path="/mcp")
    server.add_middleware.assert_called_once()
    middleware = server.add_middleware.call_args.args[0]
    assert isinstance(middleware, runtime.BearerAuthMiddleware)
    assert middleware._token == credentials[runtime.ENV_HTTP_AUTH_TOKEN]
    assert middleware._tenant_id == credentials[runtime.ENV_HTTP_TENANT_ID]
    assert all(port.startswith("127.0.0.1:") for port in service["ports"])
