"""R16: a diagnostic inside a success answer must not carry a credential.

``marketplace_sources`` reports *why* a source was skipped, and that reason is
an exception message. An exception message can quote a bearer token or a proxy
credential: the log path already scrubbed it, the answer path did not. The same
holds for the health answer and for the operator's status answers.

Offline by construction: the failing import is synthetic, and no marketplace,
browser or network is touched.
"""

from __future__ import annotations

import builtins

from fastmcp import Client
from marketplace_connector import cli, server
from mcp_core.transport import browser_handoff

TOKEN = "Bearer AUDIT_FAKE_TOKEN_123456"
PROXY = "http://proxy-user:proxy-secret@proxy.example:8080"


def _failing_import(monkeypatch, failing: str, message: str) -> None:
    """Make one connector's import fail with the given message."""
    original = builtins.__import__

    def importer(name, *args, **kwargs):
        if name == failing:
            raise ValueError(message)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(server, "__import__", importer, raising=False)


async def _skipped_reason(monkeypatch, message: str) -> str:
    """Mount with one broken connector and read the reason the tool reports."""
    monkeypatch.setattr(server, "selected", lambda: {"wildberries"})
    monkeypatch.setattr(server, "_MOUNTED", [])
    monkeypatch.setattr(server, "_SKIPPED", {})
    _failing_import(monkeypatch, "wb_connector.server", message)
    server._mount_all()

    async with Client(server.mcp) as client:
        outcome = (await client.call_tool("marketplace_sources", {})).structured_content
    return outcome["skipped"]["wildberries"]


async def test_the_skipped_reason_does_not_carry_a_bearer_token(monkeypatch):
    """The public tool's answer must not echo the credential."""
    reason = await _skipped_reason(monkeypatch, f"cannot import wildberries: {TOKEN}")

    assert "AUDIT_FAKE_TOKEN_123456" not in reason
    assert TOKEN not in reason
    assert "<redacted>" in reason


async def test_the_skipped_reason_does_not_carry_a_proxy_credential(monkeypatch):
    """A proxy URL in the message keeps its host, loses its userinfo."""
    reason = await _skipped_reason(monkeypatch, f"connect failed via {PROXY}")

    assert "proxy-secret" not in reason
    assert "proxy-user" not in reason
    assert "proxy.example" in reason


async def test_the_skipped_reason_stays_readable(monkeypatch):
    """The diagnosis an operator needs must survive the scrubbing."""
    reason = await _skipped_reason(monkeypatch, f"cannot import wildberries: {TOKEN}")

    assert "ValueError" in reason
    assert "cannot import wildberries" in reason


async def test_an_ordinary_import_failure_is_still_reported(monkeypatch):
    """The positive control: a missing dependency keeps its own wording."""
    reason = await _skipped_reason(monkeypatch, "No module named 'playwright'")

    assert "No module named 'playwright'" in reason


async def test_the_health_answer_does_not_carry_a_token_from_a_retained_page(monkeypatch):
    """A retained challenge page can carry a one-time token in its query."""
    monkeypatch.setattr(
        browser_handoff,
        "handoff_diagnostics",
        lambda: {
            "count": 1,
            "leases": [
                {
                    "url": "https://www.ozon.ru/search/?token=AUDIT_FAKE_TOKEN_123456",
                    "profile": r"C:\Users\someone\AppData\Local\Chrome-Scraping",
                }
            ],
        },
    )

    result = await server.marketplace_health()

    text = repr(result.handoffs)
    assert "AUDIT_FAKE_TOKEN_123456" not in text
    assert "someone" not in text
    # The page itself is still named, so the operator knows what to open.
    assert "ozon.ru" in text


def test_the_doctor_detail_does_not_carry_a_token():
    """The doctor's per-source detail is printed and written to a status file."""
    detail = cli._check_detail({"state": "inconclusive", "notes": [f"ValueError: {TOKEN}"]})

    assert "AUDIT_FAKE_TOKEN_123456" not in detail
    assert "<redacted>" in detail
    # The reason the check reached that state is still readable.
    assert "inconclusive" in detail
    assert "ValueError" in detail


def test_a_reason_code_is_still_shown_unchanged():
    """The positive control: the reason code the operator acts on is untouched."""
    detail = cli._check_detail({"state": "inconclusive", "reason": "transport_down", "notes": ["ignored"]})

    assert detail == "inconclusive (transport_down)"


async def test_a_failing_selfcheck_does_not_print_the_token(monkeypatch):
    """A selfcheck that raises is reported by class, with the text scrubbed."""
    original = builtins.__import__

    def importer(name, *args, **kwargs):
        if name == "wb_connector.server":
            raise ValueError(f"cannot import wildberries: {TOKEN}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(cli, "__import__", importer, raising=False)

    name, status, detail, checks = await cli._run_one_selfcheck("wildberries", "wb_connector.server", "wb_selfcheck")

    assert (name, status, checks) == ("wildberries", "error", {})
    assert "AUDIT_FAKE_TOKEN_123456" not in detail
    assert "<redacted>" in detail


def test_a_healthy_source_is_still_reported_as_healthy():
    """The positive control: the doctor detail of a healthy check is unchanged."""
    detail = cli._check_detail({"state": "healthy", "reason": "ok", "notes": ["all good"]})

    assert detail == "healthy (ok)"
